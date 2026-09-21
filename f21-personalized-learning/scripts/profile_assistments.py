#!/usr/bin/env python3
"""Create the G5-01B source manifest and privacy-safe dataset profile.

The raw CSV is read locally and never copied into an artifact. Temporary
duplicate detection uses a local SQLite database and is deleted on exit.
Composite skill tokens such as ``1_13`` are kept intact.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from io import StringIO
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


REQUIRED_COLUMNS = ("user_id", "problem_id", "correct", "order_id")
MISSING = {"", "NA", "null", "NULL", "nan", "NaN"}
INTEGER_RE = re.compile(r"^[+-]?\d+$")
NUMBER_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def git_tracked(path: Path) -> bool:
    return subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def committed_student_ids() -> bool:
    """Reject committed ID lists/maps while allowing privacy-safe column names."""
    try:
        names = subprocess.check_output(["git", "ls-files", "-z"], text=False).split(b"\0")
    except (OSError, subprocess.CalledProcessError):
        return True
    suspicious_keys = {"studentIds", "userIds", "studentIdMap", "userIdMap", "rawStudentIds"}
    for encoded in names:
        if not encoded or not (b"artifacts/" in encoded or b"data/manifests/" in encoded):
            continue
        path = Path(os.fsdecode(encoded))
        if path.suffix.lower() != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if any(key in suspicious_keys for key in value):
                    return True
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return False


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def decode_raw(path: Path) -> tuple[str, str, int, int, bool, list[str]]:
    """Decode without silently replacing bytes and return privacy-safe audit data."""
    data = path.read_bytes()
    utf8_error_count = 0
    try:
        text = data.decode("utf-8", errors="strict")
        encoding = "utf-8"
        fallback_used = False
    except UnicodeDecodeError:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        for byte in data:
            try:
                decoder.decode(bytes((byte,)), final=False)
            except UnicodeDecodeError as exc:
                utf8_error_count += max(1, exc.end - exc.start)
                decoder.reset()
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            utf8_error_count += max(1, exc.end - exc.start)
        try:
            text = data.decode("windows-1252", errors="strict")
            encoding = "windows-1252"
            fallback_used = True
        except UnicodeDecodeError:
            return "", "unknown", utf8_error_count, 0, True, ["encoding decode failure"]
    replacement_count = text.count("\ufffd")
    errors = ["replacement character present"] if replacement_count else []
    return text, encoding, utf8_error_count, replacement_count, fallback_used, errors


def is_missing(value: str | None) -> bool:
    return value is None or value.strip() in MISSING


def classify_type(flags: set[str]) -> str:
    flags = flags - {"null"}
    if not flags:
        return "null"
    if flags == {"integer"}:
        return "integer"
    if flags <= {"integer", "number"}:
        return "number"
    if flags == {"string"}:
        return "string"
    return "mixed"


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def profile(raw_path: Path) -> dict[str, Any]:
    column_missing: Counter[str] = Counter()
    column_types: dict[str, set[str]] = {}
    column_names: list[str] = []
    user_counts: Counter[str] = Counter()
    unique_problems: set[str] = set()
    unique_skills: set[str] = set()
    sequence_lengths: list[int] = []
    valid_rows = 0
    raw_rows = 0
    correct_values = 0
    correct_sum = 0
    multi_skill_rows = 0
    no_skill_rows = 0
    malformed_rows = 0

    text, source_encoding, utf8_decode_errors, replacement_count, fallback_used, encoding_errors = decode_raw(raw_path)
    if encoding_errors:
        return {
            "passed": False,
            "verifiedAt": utc_now(),
            "gitCommit": git_commit(),
            "rawRows": 0,
            "validCoreRows": 0,
            "sourceEncoding": source_encoding,
            "utf8DecodeErrorCount": utf8_decode_errors,
            "replacementCharacterCount": replacement_count,
            "encodingFallbackUsed": fallback_used,
            "errors": encoding_errors,
        }

    with tempfile.TemporaryDirectory(prefix="g5-assistments-profile-") as tmp:
        db = sqlite3.connect(Path(tmp) / "events.sqlite3")
        db.execute(
            "CREATE TABLE events (event_key BLOB PRIMARY KEY, signature BLOB NOT NULL, "
            "copies INTEGER NOT NULL DEFAULT 1, conflicts INTEGER NOT NULL DEFAULT 0)"
        )
        with StringIO(text, newline="") as handle:
            reader = csv.DictReader(handle)
            column_names = list(reader.fieldnames or [])
            column_types = {name: set() for name in column_names}
            if any(name not in column_names for name in REQUIRED_COLUMNS):
                missing = sorted(set(REQUIRED_COLUMNS) - set(column_names))
                raise ValueError(f"missing required columns: {missing}")

            for row in reader:
                raw_rows += 1
                if None in row or None in row.values():
                    malformed_rows += 1
                for name in column_names:
                    value = row.get(name)
                    if is_missing(value):
                        column_missing[name] += 1
                        column_types[name].add("null")
                    else:
                        value = value.strip()
                        if INTEGER_RE.match(value):
                            column_types[name].add("integer")
                        elif NUMBER_RE.match(value):
                            column_types[name].add("number")
                        else:
                            column_types[name].add("string")

                skill = (row.get("skill_id") or "").strip()
                if is_missing(skill):
                    no_skill_rows += 1
                elif "_" in skill:
                    multi_skill_rows += 1
                    unique_skills.add(skill)
                else:
                    unique_skills.add(skill)

                required_values = [row.get(name) for name in REQUIRED_COLUMNS]
                if any(is_missing(value) for value in required_values):
                    continue
                correct = (row.get("correct") or "").strip()
                if correct not in {"0", "1"}:
                    continue
                valid_rows += 1
                correct_values += 1
                correct_sum += int(correct)
                user = (row.get("user_id") or "").strip()
                problem = (row.get("problem_id") or "").strip()
                order = (row.get("order_id") or "").strip()
                user_counts[user] += 1
                unique_problems.add(problem)
                key_text = "\x1f".join((user, problem, order)).encode("utf-8")
                signature = hashlib.sha256(
                    json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).digest()
                event_key = hashlib.sha256(key_text).digest()
                db.execute(
                    "INSERT INTO events(event_key, signature) VALUES(?, ?) "
                    "ON CONFLICT(event_key) DO UPDATE SET "
                    "copies=copies+1, conflicts=conflicts + "
                    "CASE WHEN events.signature != excluded.signature THEN 1 ELSE 0 END",
                    (event_key, signature),
                )
                if valid_rows % 10000 == 0:
                    db.commit()
            db.commit()

        duplicate_rows, conflict_rows = db.execute(
            "SELECT COALESCE(SUM(copies - 1), 0), COALESCE(SUM(conflicts), 0) FROM events"
        ).fetchone()
        db.close()

    sequence_lengths = list(user_counts.values())
    errors: list[str] = list(encoding_errors)
    if malformed_rows:
        errors.append("malformed CSV rows")
    if valid_rows != raw_rows:
        errors.append("invalid core rows")
    if conflict_rows:
        errors.append("conflict: duplicate event keys have different content")
    if replacement_count:
        errors.append("replacement character present")
    profile = {
        "passed": malformed_rows == 0 and valid_rows == raw_rows and conflict_rows == 0 and replacement_count == 0,
        "verifiedAt": utc_now(),
        "gitCommit": git_commit(),
        "dataType": "PUBLIC_DATASET",
        "datasetName": "ASSISTments 2009-2010 Skill Builder",
        "datasetVersion": "2009-2010 Skill Builder corrected version",
        "rawRows": raw_rows,
        "validCoreRows": valid_rows,
        "columns": column_names,
        "columnTypes": {name: classify_type(column_types[name]) for name in column_names},
        "missingness": {
            name: {"count": column_missing[name], "rate": round(column_missing[name] / raw_rows, 8) if raw_rows else 0}
            for name in column_names
        },
        "uniqueCounts": {
            "students": len(user_counts),
            "problems": len(unique_problems),
            "skills": len(unique_skills),
        },
        "correctness": {
            "validValues": correct_values,
            "correctCount": correct_sum,
            "correctRate": round(correct_sum / correct_values, 8) if correct_values else None,
        },
        "perStudentSequenceLength": {
            "students": len(sequence_lengths),
            "min": min(sequence_lengths) if sequence_lengths else None,
            "max": max(sequence_lengths) if sequence_lengths else None,
            "mean": round(mean(sequence_lengths), 3) if sequence_lengths else None,
            "median": median(sequence_lengths) if sequence_lengths else None,
            "p90": percentile(sequence_lengths, 0.90),
            "p99": percentile(sequence_lengths, 0.99),
        },
        "duplicates": {
            "eventKey": "user_id + problem_id + order_id",
            "duplicateRows": int(duplicate_rows),
            "conflictingRows": int(conflict_rows),
        },
        "noSkillRows": no_skill_rows,
        "multiSkillCompositeTokenRows": multi_skill_rows,
        "multiSkillPolicy": "COMPOSITE_TOKEN_NO_SPLIT",
        "sequencePolicy": "ORDER_ID_SEQUENCE_NOT_REAL_TIME",
        "sourceEncoding": source_encoding,
        "utf8DecodeErrorCount": utf8_decode_errors,
        "replacementCharacterCount": replacement_count,
        "encodingFallbackUsed": fallback_used,
        "errors": errors,
    }
    return profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--profile-output", type=Path, required=True)
    args = parser.parse_args()

    digest, byte_count = sha256_file(args.raw)
    stat = args.raw.stat()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    profile_result = profile(args.raw)
    profile_result["input"] = {
        "relativePath": "data/raw/assistments-2009-2010-skill-builder-corrected/assistments-2009-2010-skill-builder-corrected.csv",
        "sha256": digest,
        "bytes": byte_count,
    }
    raw_tracked = git_tracked(args.raw)
    raw_ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", str(args.raw)], check=False
    ).returncode == 0
    student_ids_committed = committed_student_ids()
    metadata_downloaded = metadata.get("rawDataDownloaded") is True
    manifest = {
        "passed": profile_result["passed"],
        "verifiedAt": utc_now(),
        "gitCommit": git_commit(),
        "datasetName": "ASSISTments 2009-2010 Skill Builder",
        "datasetVersion": "2009-2010 Skill Builder corrected version",
        "correctedFileId": "1NNXHFRxcArrU0ZJSb9BIL56vmUt5FhlE",
        "officialPage": "https://sites.google.com/site/assistmentsdata/home/2009-2010-assistment-data/skill-builder-data-2009-2010",
        "termsPage": "https://sites.google.com/site/assistmentstestbed/4-analyze-data/terms-of-use-for-data",
        "rawPath": profile_result["input"]["relativePath"],
        "sha256": digest,
        "bytes": byte_count,
        "downloadedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "downloadTool": metadata.get("downloadTool", "curl -L --fail"),
        "downloadToolVersion": metadata.get("curlVersion"),
        "fileName": metadata.get("fileName"),
        "contentDisposition": metadata.get("contentDisposition"),
        "contentType": metadata.get("contentType"),
        "contentLength": metadata.get("contentLength"),
        "lastModified": metadata.get("lastModified"),
        "finalHost": metadata.get("finalHost"),
        "rawReadOnly": not bool(stat.st_mode & 0o222),
        "gitIgnored": raw_ignored,
        "gitTracked": raw_tracked,
        "rawDataCommitted": raw_tracked,
        "standardLicense": None,
        "usageBasis": "OFFICIAL_RESEARCH_USE_TERMS",
        "rawDataDownloaded": metadata_downloaded,
        "multiSkillPolicy": "COMPOSITE_TOKEN_NO_SPLIT",
        "sequencePolicy": "ORDER_ID_SEQUENCE_NOT_REAL_TIME",
        "studentIdsCommitted": student_ids_committed,
        "errors": list(profile_result["errors"]),
    }
    if not manifest["rawReadOnly"]:
        manifest["errors"].append("raw file is writable")
    if not manifest["gitIgnored"]:
        manifest["errors"].append("raw file is not git-ignored")
    if manifest["gitTracked"]:
        manifest["errors"].append("raw file is git-tracked")
    if not manifest["rawDataDownloaded"]:
        manifest["errors"].append("download metadata does not verify acquisition")
    if manifest["studentIdsCommitted"]:
        manifest["errors"].append("committed artifact contains student identifiers")
    manifest["passed"] = (
        profile_result["passed"]
        and manifest["rawReadOnly"]
        and manifest["gitIgnored"]
        and not manifest["gitTracked"]
        and not manifest["rawDataCommitted"]
        and manifest["rawDataDownloaded"]
        and not manifest["studentIdsCommitted"]
    )
    for output, value in ((args.manifest_output, manifest), (args.profile_output, profile_result)):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": manifest["passed"], "rawRows": profile_result["rawRows"], "validCoreRows": profile_result["validCoreRows"], "sha256": digest}, ensure_ascii=False))
    return 0 if manifest["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

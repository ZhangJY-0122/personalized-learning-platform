#!/usr/bin/env python3
"""Build a deterministic, privacy-safe ASSISTments cleaning and split package.

Raw and derived row-level data stay under the ignored ``data/processed`` tree.
Committed reports contain only aggregate counts and cryptographic hashes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from scripts.profile_assistments import decode_raw, sha256_file, utc_now
except ModuleNotFoundError:  # direct ``python scripts/clean_split_assistments.py`` invocation
    from profile_assistments import decode_raw, sha256_file, utc_now


REQUIRED = ("user_id", "problem_id", "correct", "order_id")
SOURCE_VERSION = "ASSISTments 2009-2010 Skill Builder corrected version"
SCHEMA_VERSION = "g5-interaction-v1"
ANON_PREFIX = "g5-assistments-v1:"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_hash(value: str, kind: str) -> str:
    return hashlib.sha256((ANON_PREFIX + kind + ":" + value).encode("utf-8")).hexdigest()


def git_commit() -> str:
    import subprocess

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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return sha256_file(path)


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    """Drop all in-memory-only fields (including raw problem sort keys)."""
    return {key: value for key, value in row.items() if not key.startswith("_")}


def order_key(value: str) -> tuple[int, Any]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, value)


def split_counts(length: int) -> tuple[int, int, int]:
    """Floor the ratios while guaranteeing 3/1/1 for eligible sequences."""
    train = max(3, int(length * 0.70))
    validation = max(1, int(length * 0.10))
    if train + validation >= length:
        validation = 1
        train = length - 2
    test = length - train - validation
    if test < 1:
        train -= 1
        test = length - train - validation
    return train, validation, test


def clean_rows(raw_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    text, source_encoding, utf8_errors, replacements, fallback, encoding_errors = decode_raw(raw_path)
    if encoding_errors or replacements:
        raise ValueError("source encoding gate failed")
    reader = csv.DictReader(text.splitlines(keepends=True))
    columns = list(reader.fieldnames or [])
    if any(name not in columns for name in REQUIRED):
        raise ValueError("source schema missing required columns")

    retained: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str], str] = {}
    maps = {"student": {}, "item": {}, "skill": {}}
    reasons: Counter[str] = Counter()
    duplicate_rows = 0
    conflict_rows = 0
    raw_rows = 0
    for source_row, row in enumerate(reader, start=2):
        raw_rows += 1
        if None in row or None in row.values():
            reasons["malformed_csv"] += 1
            continue
        values = {key: (value.strip() if isinstance(value, str) else value) for key, value in row.items()}
        if any(not values.get(name) for name in REQUIRED):
            reasons["missing_core_field"] += 1
            continue
        if values["correct"] not in {"0", "1"}:
            reasons["invalid_correct"] += 1
            continue
        user_id = values["user_id"]
        problem_id = values["problem_id"]
        order_id = values["order_id"]
        event_key = (user_id, problem_id, order_id)
        signature = digest_bytes(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if event_key in seen:
            if seen[event_key] == signature:
                duplicate_rows += 1
                reasons["exact_duplicate"] += 1
                continue
            conflict_rows += 1
            reasons["conflicting_event_key"] += 1
            continue
        seen[event_key] = signature
        skill = values.get("skill_id") or "<UNK_SKILL>"
        maps["student"][user_id] = stable_hash(user_id, "student")
        maps["item"][problem_id] = stable_hash(problem_id, "item")
        maps["skill"][skill] = stable_hash(skill, "skill") if skill != "<UNK_SKILL>" else "<UNK_SKILL>"
        retained.append({
            "_problemSort": order_key(problem_id),
            "sourceRow": source_row,
            "studentExternalId": maps["student"][user_id],
            "itemExternalId": maps["item"][problem_id],
            "skillExternalId": maps["skill"][skill],
            "skillToken": skill,
            "orderId": order_id,
            "answer": values.get("answer_id") or None,
            "correct": int(values["correct"]),
            "sourceDataset": "ASSISTments",
            "sourceVersion": SOURCE_VERSION,
        })

    retained.sort(key=lambda row: (row["studentExternalId"], order_key(row["orderId"]), row["_problemSort"], row["sourceRow"]))
    cleaning = {
        "rawRows": raw_rows,
        "retainedRows": len(retained),
        "filteredRows": raw_rows - len(retained),
        "filterReasons": dict(sorted(reasons.items())),
        "duplicateRowsFiltered": duplicate_rows,
        "conflictingRows": conflict_rows,
        "columns": columns,
        "sourceEncoding": source_encoding,
        "utf8DecodeErrorCount": utf8_errors,
        "replacementCharacterCount": replacements,
        "encodingFallbackUsed": fallback,
    }
    return retained, cleaning, maps


def build_splits(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["studentExternalId"]].append(row)
    assignments: list[dict[str, Any]] = []
    partition_counts = Counter()
    short_students = 0
    eligible_students = 0
    for student in sorted(grouped):
        sequence = sorted(grouped[student], key=lambda row: (order_key(row["orderId"]), row["_problemSort"], row["sourceRow"]))
        if len(sequence) < 5:
            short_students += 1
            for index, row in enumerate(sequence):
                assignments.append({"studentExternalId": student, "sourceRow": row["sourceRow"], "partition": "SHORT_COVERAGE", "sequenceIndex": index})
                partition_counts["SHORT_COVERAGE"] += 1
            continue
        eligible_students += 1
        train, validation, test = split_counts(len(sequence))
        boundaries = [("train", train), ("validation", validation), ("test", test)]
        offset = 0
        for partition, count in boundaries:
            for index, row in enumerate(sequence[offset:offset + count], start=offset):
                assignments.append({"studentExternalId": student, "sourceRow": row["sourceRow"], "partition": partition, "sequenceIndex": index})
                partition_counts[partition] += 1
            offset += count
    assignment_rows = sorted(assignments, key=lambda row: (row["studentExternalId"], row["sequenceIndex"], row["partition"], row["sourceRow"]))
    partition_hashes: dict[str, str] = {}
    for partition in sorted(partition_counts):
        payload = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            for row in assignment_rows
            if row["partition"] == partition
        )
        partition_hashes[partition] = digest_bytes(payload)
    return assignment_rows, {
        "students": len(grouped),
        "eligibleStudents": eligible_students,
        "shortStudents": short_students,
        "partitionRows": dict(sorted(partition_counts.items())),
        "partitionHashes": partition_hashes,
    }


def processed_file_status(paths: list[Path]) -> dict[str, Any]:
    statuses = []
    per_file = {}
    for path in paths:
        ignored = subprocess.run(["git", "check-ignore", "--quiet", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
        tracked = git_tracked(path)
        statuses.append((ignored, tracked))
        per_file[str(path)] = {"gitIgnored": ignored, "gitTracked": tracked, "committed": tracked}
    return {
        "gitIgnored": all(ignored for ignored, _ in statuses),
        "gitTracked": any(tracked for _, tracked in statuses),
        "committed": any(tracked for _, tracked in statuses),
        "files": per_file,
    }


def validate_split_invariants(
    rows: list[dict[str, Any]], assignments: list[dict[str, Any]], processed_paths: list[Path],
) -> dict[str, Any]:
    row_sources = [row["sourceRow"] for row in rows]
    assignment_sources = [row["sourceRow"] for row in assignments]
    source_counts = Counter(assignment_sources)
    by_student: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        by_student[assignment["studentExternalId"]].append(assignment)
    assigned_exactly_once = len(assignments) == len(rows) and all(source_counts[source] == 1 for source in row_sources)
    coverage_complete = set(assignment_sources) == set(row_sources)
    student_sequence_unique = len({(a["studentExternalId"], a["sequenceIndex"]) for a in assignments}) == len(assignments)
    partition_names = {"train", "validation", "test", "SHORT_COVERAGE"}
    partitions_valid = all(a["partition"] in partition_names for a in assignments)
    sequence_contiguous = True
    partition_order_valid = True
    short_isolated = True
    source_order_preserved = True
    row_lookup = {row["sourceRow"]: row for row in rows}
    for student, student_assignments in by_student.items():
        ordered = sorted(student_assignments, key=lambda a: a["sequenceIndex"])
        indices = [a["sequenceIndex"] for a in ordered]
        sequence_contiguous &= indices == list(range(len(indices)))
        student_rows = [row_lookup[a["sourceRow"]] for a in ordered if a["sourceRow"] in row_lookup]
        expected_rows = sorted(
            [row for row in rows if row["studentExternalId"] == student],
            key=lambda row: (order_key(row["orderId"]), row["_problemSort"], row["sourceRow"]),
        )
        source_order_preserved &= [row["sourceRow"] for row in student_rows] == [row["sourceRow"] for row in expected_rows]
        partitions = [a["partition"] for a in ordered]
        if len(expected_rows) < 5:
            short_isolated &= set(partitions) == {"SHORT_COVERAGE"}
        else:
            short_isolated &= "SHORT_COVERAGE" not in partitions
            partition_order_valid &= partitions == sorted(partitions, key={"train": 0, "validation": 1, "test": 2, "SHORT_COVERAGE": 3}.get)
            counts = Counter(partitions)
            partition_order_valid &= counts["train"] >= 3 and counts["validation"] >= 1 and counts["test"] >= 1
    processed = processed_file_status(processed_paths)
    partition_rows = Counter(a["partition"] for a in assignments)
    result = {
        "assignedRows": len(assignments),
        "retainedRows": len(rows),
        "assignedExactlyOnce": assigned_exactly_once,
        "coverageComplete": coverage_complete,
        "noExtraOrMissingSourceRows": coverage_complete,
        "studentSequenceUnique": student_sequence_unique,
        "noDuplicateStudentSequence": student_sequence_unique,
        "partitionsDisjoint": partitions_valid and assigned_exactly_once,
        "sequenceIndexContiguous": sequence_contiguous,
        "partitionOrderValid": partition_order_valid,
        "shortCoverageIsolated": short_isolated,
        "sourceOrderPreserved": source_order_preserved,
        "partitionRowsSumValid": sum(partition_rows.values()) == len(rows),
        "assignedRowsEqualsRetained": len(assignments) == len(rows),
        "partitionRows": dict(sorted(partition_rows.items())),
        "processedFilesIgnored": processed["gitIgnored"] and not processed["gitTracked"] and not processed["committed"],
        "processedFileStatus": processed,
    }
    result["passed"] = all(result[key] for key in (
        "assignedExactlyOnce", "coverageComplete", "noExtraOrMissingSourceRows", "studentSequenceUnique", "noDuplicateStudentSequence",
        "partitionsDisjoint", "sequenceIndexContiguous", "partitionOrderValid", "shortCoverageIsolated",
        "sourceOrderPreserved", "partitionRowsSumValid", "assignedRowsEqualsRetained", "processedFilesIgnored",
    ))
    return result


def environment() -> dict[str, str]:
    return {
        "pythonVersion": sys.version,
        "pythonImplementation": platform.python_implementation(),
        "os": platform.system(),
        "osRelease": platform.release(),
        "machine": platform.machine(),
    }


def reproduce_command() -> str:
    return (
        "python3 -m scripts.clean_split_assistments --raw "
        "data/raw/assistments-2009-2010-skill-builder-corrected/assistments-2009-2010-skill-builder-corrected.csv "
        "--source-manifest data/manifests/assistments-2009-2010-corrected.json "
        "--clean-output data/processed/assistments-clean.jsonl "
        "--map-output data/processed/external_id_map.json "
        "--cleaning-report artifacts/g5-cleaning-report.json "
        "--split-output data/processed/g5-split-assignments.jsonl "
        "--split-report artifacts/g5-split-report.json "
        "--split-manifest data/manifests/g5-split-manifest.json"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--clean-output", type=Path, required=True)
    parser.add_argument("--map-output", type=Path, required=True)
    parser.add_argument("--cleaning-report", type=Path, required=True)
    parser.add_argument("--split-output", type=Path, required=True)
    parser.add_argument("--split-report", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    args = parser.parse_args()

    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    digest, bytes_count = sha256_file(args.raw)
    if source_manifest.get("passed") is not True or source_manifest.get("sha256") != digest:
        raise SystemExit("source manifest gate failed")
    rows, cleaning, maps = clean_rows(args.raw)
    clean_hash, clean_bytes = write_jsonl(args.clean_output, [public_row(row) for row in rows])
    map_payload = {
        "algorithm": "SHA-256(domain-separated public ID)",
        "version": "g5-assistments-v1",
        "entries": {kind: len(values) for kind, values in maps.items()},
        "maps": maps,
    }
    map_bytes = json.dumps(map_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    args.map_output.parent.mkdir(parents=True, exist_ok=True)
    args.map_output.write_bytes(map_bytes)
    map_hash = digest_bytes(map_bytes)
    assignments, split_counts_report = build_splits(rows)
    split_hash, split_bytes = write_jsonl(args.split_output, assignments)
    processed_paths = [args.clean_output, args.map_output, args.split_output]
    invariants = validate_split_invariants(rows, assignments, processed_paths)
    runtime = environment()
    command = reproduce_command()
    cleaning_status = processed_file_status(processed_paths)
    invariant_errors = [name for name, passed in invariants.items() if name not in {"assignedRows", "retainedRows", "partitionRows", "processedFileStatus", "passed"} and passed is False]
    cleaning_report = {
        "passed": cleaning["conflictingRows"] == 0 and cleaning["retainedRows"] > 0 and invariants["processedFilesIgnored"],
        "verifiedAt": utc_now(),
        "gitCommit": git_commit(),
        "generator": {"script": "scripts/clean_split_assistments.py", "schemaVersion": SCHEMA_VERSION},
        "reproduceCommand": command,
        "environment": runtime,
        "schemaVersion": SCHEMA_VERSION,
        "input": {"rawPath": str(args.raw), "sha256": digest, "bytes": bytes_count, "sourceManifest": str(args.source_manifest)},
        "sourceVersion": SOURCE_VERSION,
        "inputSha256": digest,
        "inputRawSha256": digest,
        "cleanSha256": clean_hash,
        "mapSha256": map_hash,
        "splitSha256": split_hash,
        "partitionHashes": split_counts_report["partitionHashes"],
        "cleaning": cleaning,
        "externalIdMap": {"path": str(args.map_output), "sha256": map_hash, "bytes": len(map_bytes), "algorithm": map_payload["algorithm"], "entries": map_payload["entries"], "gitIgnored": cleaning_status["gitIgnored"], "gitTracked": git_tracked(args.map_output), "committed": cleaning_status["committed"]},
        "output": {"path": str(args.clean_output), "sha256": clean_hash, "bytes": clean_bytes, "gitIgnored": cleaning_status["gitIgnored"], "gitTracked": git_tracked(args.clean_output), "committed": cleaning_status["committed"]},
        "processedFiles": cleaning_status["files"],
        "errors": [] if cleaning["conflictingRows"] == 0 else ["conflicting event keys detected"],
    }
    split_report = {
        "passed": cleaning_report["passed"] and invariants["passed"],
        "verifiedAt": utc_now(),
        "gitCommit": git_commit(),
        "generator": {"script": "scripts/clean_split_assistments.py", "schemaVersion": SCHEMA_VERSION},
        "reproduceCommand": command,
        "environment": runtime,
        "schemaVersion": SCHEMA_VERSION,
        "inputRawSha256": digest,
        "cleanSha256": clean_hash,
        "mapSha256": map_hash,
        "splitSha256": split_hash,
        "partitionHashes": split_counts_report["partitionHashes"],
        "inputCleanSha256": clean_hash,
        "protocol": {"groupBy": "studentExternalId", "orderBy": ["order_id", "problem_id", "sourceRow"], "ratios": {"train": 0.70, "validation": 0.10, "test": 0.20}, "rounding": "floor with minimum 3/1/1 for sequences length >=5", "minimumMainSequenceLength": 5, "sequencePolicy": "ORDER_ID_SEQUENCE_NOT_REAL_TIME", "seeds": [11, 22, 33]},
        **split_counts_report,
        "partitionRowsHash": split_hash,
        "splitOutput": {"path": str(args.split_output), "sha256": split_hash, "bytes": split_bytes, "committed": git_tracked(args.split_output)},
        "invariants": invariants,
        "errors": invariant_errors,
    }
    split_manifest = {
        "passed": split_report["passed"],
        "verifiedAt": utc_now(),
        "gitCommit": git_commit(),
        "generator": {"script": "scripts/clean_split_assistments.py", "schemaVersion": SCHEMA_VERSION},
        "reproduceCommand": command,
        "environment": runtime,
        "schemaVersion": SCHEMA_VERSION,
        "inputRawSha256": digest,
        "inputCleanSha256": clean_hash,
        "splitSha256": split_hash,
        "splitAssignmentSha256": split_hash,
        "partitionRows": split_counts_report["partitionRows"],
        "partitionRowsHash": split_hash,
        "cleanSha256": clean_hash,
        "mapSha256": map_hash,
        "partitionHashes": split_counts_report["partitionHashes"],
        "invariants": invariants,
        "containsRawStudentIds": False,
        "containsExternalIdList": False,
        "protocol": split_report["protocol"],
        "errors": invariant_errors,
    }
    cleaning_report["passed"] = cleaning_report["passed"] and invariants["passed"]
    split_report["passed"] = cleaning_report["passed"] and invariants["passed"]
    split_manifest["passed"] = split_report["passed"]
    write_json(args.cleaning_report, cleaning_report)
    write_json(args.split_report, split_report)
    write_json(args.split_manifest, split_manifest)
    print(json.dumps({"passed": cleaning_report["passed"] and split_report["passed"], "cleanSha256": clean_hash, "splitSha256": split_hash}, ensure_ascii=False))
    return 0 if cleaning_report["passed"] and split_report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

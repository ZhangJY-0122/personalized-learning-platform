#!/usr/bin/env python3
"""Import all G5 demo catalogs through the real API in an isolated empty volume."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/g5-demo-catalog-import.json"
PROJECT = f"f21-g5-catalog-{uuid.uuid4().hex[:8]}"
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.yaml", "-f", "tests/compose-g1.yaml"]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


SERVER_PORT = free_port()
ENV = {
    **os.environ,
    "SERVER_PORT": str(SERVER_PORT),
    "WEB_PORT": str(free_port()),
    "WORKER_ENABLED": "false",
    "JWT_SECRET": uuid.uuid4().hex + uuid.uuid4().hex,
}
BASE = f"http://127.0.0.1:{SERVER_PORT}/api/v1"


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, env=ENV, text=True, capture_output=True, check=check)


def request(path: str, body=None, token: str | None = None, key: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["Idempotency-Key"] = key
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = Request(BASE + path, data=payload, headers=headers, method="POST" if body is not None else "GET")
    try:
        response = urlopen(req, timeout=30)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


def sql(query: str) -> str:
    result = compose(
        "exec", "-T", "mysql", "mysql", "-uf21", "-pf21_local_demo_only", "-Df21_prep", "-N", "-B", "-e", query
    )
    return result.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    manifest = json.loads((ROOT / "data/demo/catalogs/manifest.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()
    results = []
    checks = {}
    try:
        compose("up", "-d", "--build", "mysql", "server")
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                status, health = request("/health")
                if status == 200 and health.get("data", {}).get("database") == "UP":
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("isolated server did not become healthy")
        status, login = request("/auth/login", {"username": "admin01", "password": "Learn@12345"})
        if status != 200:
            raise RuntimeError(f"admin login failed: {status}")
        token = login["data"]["accessToken"]
        for entry in manifest["courses"]:
            package_path = ROOT / entry["path"]
            package = json.loads(package_path.read_text())
            status, response = request("/admin/catalog-imports", package, token, str(uuid.uuid4()))
            data = response.get("data", {})
            if status != 200 or data.get("status") != "SUCCEEDED":
                raise RuntimeError(f"catalog import failed for {entry['slug']}: {status} {response}")
            results.append({
                "slug": entry["slug"],
                "courseId": entry["courseId"],
                "catalogVersion": entry["catalogVersion"],
                "packageSha256": sha256(package_path),
                "jobId": data["jobId"],
                "status": data["status"],
                "publishMode": data["publishMode"],
            })
        java_entry = next(x for x in manifest["courses"] if x["slug"] == "java")
        java_package = json.loads((ROOT / java_entry["path"]).read_text())
        status, replay = request("/admin/catalog-imports", java_package, token, str(uuid.uuid4()))
        checks["identicalPayloadIsIdempotent"] = status == 200 and replay.get("data", {}).get("status") == "SUCCEEDED" and replay["data"]["jobId"] == results[0]["jobId"]
        original_hash = sql("SELECT content_hash FROM catalog_snapshot WHERE course_id='10000000-0000-4000-8000-000000000001' AND catalog_version='java-g5-v1';")
        conflicting = json.loads(json.dumps(java_package))
        conflicting["course"]["description"] += " conflicting mutation"
        status, conflict = request("/admin/catalog-imports", conflicting, token, str(uuid.uuid4()))
        checks["sameVersionDifferentContentRejected"] = status == 200 and conflict.get("data", {}).get("status") == "VALIDATION_FAILED" and conflict["data"].get("errorCode") == "CONFLICT"
        checks["conflictDidNotOverwriteSnapshot"] = original_hash == sql("SELECT content_hash FROM catalog_snapshot WHERE course_id='10000000-0000-4000-8000-000000000001' AND catalog_version='java-g5-v1';")
        checks["legacyJavaSnapshotRetained"] = sql("SELECT COUNT(*) FROM catalog_snapshot WHERE course_id='10000000-0000-4000-8000-000000000001' AND catalog_version='java-g1-v1';") == "1"
        checks["javaHasTwoCatalogVersions"] = sql("SELECT COUNT(*) FROM catalog_snapshot WHERE course_id='10000000-0000-4000-8000-000000000001' AND catalog_version IN ('java-g1-v1','java-g5-v1');") == "2"
        current_rows = sql("SELECT course_id,catalog_version FROM course WHERE course_id IN ('10000000-0000-4000-8000-000000000001','6bae72c8-2097-51bd-9272-6a8bbca2ac76','666c0270-bd10-5bed-842b-5af1360b9c06') ORDER BY course_id;").splitlines()
        checks["threeCoursesActiveAtExpectedVersions"] = current_rows == [
            "10000000-0000-4000-8000-000000000001\tjava-g5-v1",
            "666c0270-bd10-5bed-842b-5af1360b9c06\tcomputer-networks-g5-v1",
            "6bae72c8-2097-51bd-9272-6a8bbca2ac76\tdata-structures-g5-v1",
        ]
        db_counts = {}
        for entry in manifest["courses"]:
            course, version = entry["courseId"], entry["catalogVersion"]
            values = sql(
                "SELECT "
                f"(SELECT COUNT(*) FROM chapter_snapshot WHERE course_id='{course}' AND catalog_version='{version}'),"
                f"(SELECT COUNT(*) FROM knowledge_snapshot WHERE course_id='{course}' AND catalog_version='{version}'),"
                f"(SELECT COUNT(*) FROM resource_snapshot WHERE course_id='{course}' AND catalog_version='{version}'),"
                f"(SELECT COUNT(*) FROM question_snapshot WHERE course_id='{course}' AND catalog_version='{version}'),"
                f"(SELECT COUNT(*) FROM prerequisite_snapshot WHERE course_id='{course}' AND catalog_version='{version}');"
            ).split("\t")
            db_counts[entry["slug"]] = dict(zip(("chapters", "knowledgePoints", "resources", "questions", "prerequisites"), map(int, values)))
            expected = entry["counts"]
            checks[f"{entry['slug']}SnapshotCountsMatch"] = db_counts[entry["slug"]] == {
                "chapters": expected["chapters"],
                "knowledgePoints": expected["knowledgePoints"],
                "resources": expected["resources"],
                "questions": expected["questions"],
                "prerequisites": expected["prerequisites"],
            }
        passed = all(checks.values())
        artifact = {
            "passed": passed,
            "schemaVersion": "g5-demo-catalog-import-v1",
            "catalogCommit": commit,
            "verifiedAt": utc_now(),
            "dataType": "SYNTHETIC_DEMO",
            "isolation": {"composeProject": PROJECT, "volume": f"{PROJECT}_f21-data", "serverPort": SERVER_PORT, "freshVolume": True},
            "imports": results,
            "databaseSnapshotCounts": db_counts,
            "checks": checks,
            "gapReport": [] if passed else [name for name, value in checks.items() if not value],
            "limitations": ["The isolated Compose containers and network are removed after validation; the named audit volume is retained."],
        }
        ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        if not passed:
            raise RuntimeError(f"catalog import checks failed: {artifact['gapReport']}")
        print(json.dumps({"passed": True, "schemaVersion": artifact["schemaVersion"], "project": PROJECT}, sort_keys=True))
    finally:
        compose("down", check=False)


if __name__ == "__main__":
    main()

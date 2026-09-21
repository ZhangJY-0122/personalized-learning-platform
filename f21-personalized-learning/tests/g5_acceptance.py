#!/usr/bin/env python3
"""Lightweight G5-08 end-to-end acceptance in a fresh Compose volume.

This deliberately covers one real happy-path learning loop per G5 demo course.
It does not replace the preserved G1--G4 isolation suites or fabricate formal
research results.  The temporary enrollment and teacher scope rows are test
fixture access setup; the learning, recommendation, path and dashboard checks
all use the public HTTP API.
"""
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
ARTIFACT = ROOT / "artifacts/g5-acceptance.json"
STUDENT_ID = "3d7a8826-cee4-525a-98d7-8cdb937e677e"
TEACHER_ID = "20000000-0000-4000-8000-000000000003"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Stack:
    def __init__(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.project = f"f21-g5-acceptance-{suffix}"
        self.server_port = free_port()
        self.web_port = free_port()
        self.base = f"http://127.0.0.1:{self.server_port}/api/v1"
        self.env = {
            **os.environ,
            "SERVER_PORT": str(self.server_port),
            "WEB_PORT": str(self.web_port),
            "WORKER_ENABLED": "true",
            "JWT_SECRET": f"g5-acceptance-{uuid.uuid4().hex}{uuid.uuid4().hex}",
        }
        self.command = ["docker", "compose", "-p", self.project, "-f", "compose.yaml", "-f", "tests/compose-g1.yaml"]
        self.http_checks = 0
        self.checks: dict[str, bool] = {}

    def compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(self.command + list(args), cwd=ROOT, env=self.env, capture_output=True, text=True)
        if check and result.returncode:
            raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])
        return result

    def request(self, method: str, path: str, token: str | None = None, body=None, key: str | None = None, expected=200):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if key:
            headers["Idempotency-Key"] = key
        request = Request(
            self.base + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
            headers=headers,
            method=method,
        )
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            payload = json.loads(response.read())
            self.http_checks += 1
            allowed = expected if isinstance(expected, tuple) else (expected,)
            if response.status not in allowed:
                raise AssertionError((method, path, response.status, allowed, payload))
            if "traceId" not in payload or response.headers.get("X-Trace-Id") != payload["traceId"]:
                raise AssertionError(("trace mismatch", path, payload))
            return payload.get("data", payload)

    def get(self, path: str, token: str | None = None, expected=200):
        return self.request("GET", path, token, expected=expected)

    def post(self, path: str, token: str | None = None, body=None, key: str | None = None, expected=200):
        return self.request("POST", path, token, body, key, expected)

    def login(self, username: str) -> str:
        return self.post("/auth/login", body={"username": username, "password": "Learn@12345"})["accessToken"]

    def sql(self, query: str) -> str:
        return self.compose(
            "exec", "-T", "mysql", "mysql", "-uf21", "-pf21_local_demo_only", "-Df21_prep", "-N", "-B", "-e", query
        ).stdout.strip()

    def up(self) -> None:
        self.compose("up", "-d", "--build")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                if self.get("/health")["database"] == "UP":
                    return
            except Exception:
                pass
            time.sleep(1)
        logs = self.compose("logs", "--tail", "120", "server", check=False)
        raise RuntimeError(f"fresh stack did not become healthy:\n{logs.stdout[-6000:]}")

    def down(self) -> None:
        self.compose("down", check=False)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def wait_submission(stack: Stack, token: str, submission_id: str) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = stack.get(f"/practice/submissions/{submission_id}", token)
        if result["processingStatus"] == "SUCCEEDED":
            return result
        if result["processingStatus"] == "FAILED":
            raise AssertionError(("submission failed", result))
        time.sleep(0.5)
    raise AssertionError(("submission timed out", submission_id))


def main() -> None:
    stack = Stack()
    report: dict = {
        "passed": False,
        "schemaVersion": "g5-acceptance-v1",
        "gitCommit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip(),
        "verifiedAt": utc_now(),
        "dataType": "SYNTHETIC_DEMO",
        "scope": "Fresh-volume happy-path acceptance for three demo courses, dashboards and role boundary.",
        "isolation": {
            "composeProject": stack.project,
            "volume": f"{stack.project}_f21-data",
            "serverPort": stack.server_port,
            "webPort": stack.web_port,
            "freshVolume": True,
        },
        "courses": [],
        "checks": stack.checks,
        "security": {"credentialsRecorded": False, "tokensRecorded": False, "answersRecorded": False, "studentIdentifiersRecorded": False},
        "limitations": [
            "This is a lightweight G5-08 happy-path acceptance, not a replacement for the preserved G1-G4 fault-injection suites.",
            "Teacher scope and student enrollment for newly imported courses are isolated test-fixture setup because no enrollment-management API is in the G5 scope.",
        ],
    }
    succeeded = False
    try:
        manifest = json.loads((ROOT / "data/demo/catalogs/manifest.json").read_text())
        packages = []
        answers: dict[str, list[str]] = {}
        for entry in manifest["courses"]:
            package_path = ROOT / entry["path"]
            package = json.loads(package_path.read_text())
            packages.append((entry, package_path, package))
            answers.update({question["questionId"]: question["answer"] for question in package["questions"]})

        stack.up()
        student = stack.login("student01")
        teacher = stack.login("teacher01")
        admin = stack.login("admin01")
        stack.checks["healthDatabaseUp"] = True

        for entry, package_path, package in packages:
            imported = stack.post("/admin/catalog-imports", admin, package, str(uuid.uuid4()))
            if imported.get("status") != "SUCCEEDED":
                raise AssertionError(("catalog import", entry["slug"], imported))
            course_id = entry["courseId"]
            stack.sql(
                "INSERT INTO course_enrollment(user_id,course_id,status) VALUES "
                f"('{STUDENT_ID}','{course_id}','ACTIVE') ON DUPLICATE KEY UPDATE status='ACTIVE'"
            )
            stack.sql(
                "INSERT INTO teacher_course_scope(user_id,course_id,status) VALUES "
                f"('{TEACHER_ID}','{course_id}','ACTIVE') ON DUPLICATE KEY UPDATE status='ACTIVE'"
            )
            report["courses"].append({
                "slug": entry["slug"],
                "courseId": course_id,
                "catalogVersion": entry["catalogVersion"],
                "catalogSha256": sha256(package_path),
                "importStatus": imported["status"],
            })

        enrollments = stack.get(f"/students/{STUDENT_ID}/enrollments", student)
        imported_ids = {course["courseId"] for course in report["courses"]}
        stack.checks["studentCanSeeThreeImportedCourses"] = imported_ids.issubset({item["courseId"] for item in enrollments["items"]})
        stack.checks["legacyJavaSnapshotRetained"] = stack.sql(
            "SELECT COUNT(*) FROM catalog_snapshot WHERE course_id='10000000-0000-4000-8000-000000000001' AND catalog_version='java-g1-v1'"
        ) == "1"

        for course in report["courses"]:
            course_id = course["courseId"]
            structure = stack.get(f"/courses/{course_id}/structure", student)
            resources = stack.get(f"/courses/{course_id}/resources", student)["items"]
            questions = stack.get(f"/courses/{course_id}/questions", student)["items"]
            if len(structure["knowledgePoints"]) != 15 or not resources or not questions:
                raise AssertionError(("catalog not usable", course["slug"], structure, len(resources), len(questions)))

            recommendation = stack.post(
                f"/students/{STUDENT_ID}/recommendations/generate", student, {"courseId": course_id}, str(uuid.uuid4())
            )
            resource_recommendation = next(item for item in recommendation["items"] if item["itemType"] == "RESOURCE")
            resource_id = resource_recommendation["itemId"]
            stack.get(f"/resources/{resource_id}", student)
            stack.post(f"/resources/{resource_id}/views", student, {"recommendationId": resource_recommendation["recommendationId"]}, str(uuid.uuid4()))
            completed = stack.post(f"/resources/{resource_id}/completions", student, {"recommendationId": resource_recommendation["recommendationId"]}, str(uuid.uuid4()))
            stack.post(
                f"/recommendations/{resource_recommendation['recommendationId']}/feedback",
                student,
                {"feedbackType": "COMPLETED", "sourceEventId": completed["sourceEventId"]},
                str(uuid.uuid4()),
            )

            path = stack.post(f"/students/{STUDENT_ID}/learning-path/generate", student, {"courseId": course_id}, str(uuid.uuid4()))
            node = next(item for item in path["nodes"] if item["itemType"] == "RESOURCE" and item["actionable"])
            stack.post(f"/resources/{node['itemId']}/views", student, {"pathNodeId": node["nodeId"]}, str(uuid.uuid4()))
            stack.post(f"/resources/{node['itemId']}/completions", student, {"pathNodeId": node["nodeId"]}, str(uuid.uuid4()))
            latest_path = stack.get(f"/students/{STUDENT_ID}/learning-path?courseId={course_id}", student)
            question_node = next(item for item in latest_path["nodes"] if item["itemType"] == "QUESTION" and item["actionable"])
            submitted = stack.post(
                "/practice/submissions",
                student,
                {
                    "questionId": question_node["itemId"],
                    "answer": answers[question_node["itemId"]],
                    "catalogVersion": course["catalogVersion"],
                    "pathNodeId": question_node["nodeId"],
                },
                str(uuid.uuid4()),
            )
            result = wait_submission(stack, student, submitted["submissionId"])
            mastery = stack.get(f"/students/{STUDENT_ID}/mastery?courseId={course_id}", student)
            course.update({
                "recommendationItems": len(recommendation["items"]),
                "pathId": path["pathId"],
                "pathStatusAfterOneQuestion": stack.get(f"/students/{STUDENT_ID}/learning-path?courseId={course_id}", student)["status"],
                "submissionSucceeded": result["processingStatus"] == "SUCCEEDED",
                "masteryItems": len(mastery["items"]),
            })

        stack.checks["allCourseLearningLoopsSucceeded"] = all(course["submissionSucceeded"] for course in report["courses"])
        teacher_courses = stack.get("/courses", teacher)
        stack.checks["teacherCanSeeThreeCourses"] = imported_ids.issubset({item["courseId"] for item in teacher_courses["items"]})
        for course in report["courses"]:
            course_id = course["courseId"]
            weakness = stack.get(f"/courses/{course_id}/weakness-statistics", teacher)
            metrics = stack.get(f"/courses/{course_id}/recommendation-metrics", teacher)
            if weakness["courseId"] != course_id or len(metrics["metrics"]) != 4:
                raise AssertionError(("dashboard data", course["slug"], weakness, metrics))
        models = stack.get("/models", teacher)
        experiments = stack.get("/experiments", admin)
        stack.checks["teacherAndAdminDashboardsReadable"] = bool(models["items"]) and bool(experiments["items"])
        stack.get(f"/courses/{report['courses'][0]['courseId']}/weakness-statistics", student, expected=403)
        stack.get("/models", student, expected=403)
        stack.checks["studentDashboardAccessDenied"] = True
        stack.checks["allChecksPassed"] = all(stack.checks.values())
        if not stack.checks["allChecksPassed"]:
            raise AssertionError(stack.checks)
        report["passed"] = True
        succeeded = True
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        report["httpChecks"] = stack.http_checks
        report["verifiedAt"] = utc_now()
        report["isolation"]["containersRemoved"] = not bool(os.environ.get("KEEP_G5_ACCEPTANCE_STACK"))
        report["isolation"]["volumeRetained"] = f"{stack.project}_f21-data"
        ARTIFACT.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        if not os.environ.get("KEEP_G5_ACCEPTANCE_STACK"):
            stack.down()
        if succeeded:
            print(json.dumps({"passed": True, "project": stack.project, "httpChecks": stack.http_checks}, ensure_ascii=False))


if __name__ == "__main__":
    main()

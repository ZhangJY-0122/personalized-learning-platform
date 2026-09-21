"""G4C-00/G4C-01 check: resource path completion reproduces, then verifies the fix.

This test owns an ephemeral Compose project and volume.  It intentionally
 By default it expects the pre-V7 HTTP 500 defect. Set G4_EXPECT_STATUS=200
 after the V7 repair to run the same scenario as a regression check.
"""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "f21-g4-repro-" + uuid.uuid4().hex[:8]
SERVER_PORT = os.environ.get("G4_REPRO_SERVER_PORT", "18087")
WEB_PORT = os.environ.get("G4_REPRO_WEB_PORT", "15177")
EXPECTED_STATUS = int(os.environ.get("G4_EXPECT_STATUS", "500"))
BASE = f"http://127.0.0.1:{SERVER_PORT}/api/v1"
USER = "3d7a8826-cee4-525a-98d7-8cdb937e677e"
COURSE = "10000000-0000-4000-8000-000000000001"
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.yaml", "-f", "tests/compose-g1.yaml"]
ENV = {
    **os.environ,
    "SERVER_PORT": SERVER_PORT,
    "WEB_PORT": WEB_PORT,
    "WORKER_ENABLED": "false",
    "JWT_SECRET": uuid.uuid4().hex + uuid.uuid4().hex,
}


def compose(*args, check=True):
    return subprocess.run(
        COMPOSE + list(args), cwd=ROOT, env=ENV, check=check,
        capture_output=True, text=True,
    )


def request(path, token=None, body=None, method=None, idempotency_key=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    encoded = None if body is None else json.dumps(body).encode()
    req = Request(BASE + path, data=encoded, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=15)
    except HTTPError as exc:
        response = exc
    with response:
        value = json.loads(response.read())
        return response.status, value


def wait_for_health(timeout=120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            status, value = request("/health")
            if status == 200 and value.get("data", {}).get("database") == "UP":
                return
            last = (status, value)
        except Exception as exc:  # startup connection failures are expected
            last = repr(exc)
        time.sleep(2)
    raise AssertionError(f"isolated server did not become healthy: {last}")


def login(username):
    status, value = request("/auth/login", body={"username": username, "password": "Learn@12345"}, method="POST")
    assert status == 200, (status, value)
    return value["data"]["accessToken"]


def mysql_query(sql):
    result = compose(
        "exec", "-T", "mysql", "mysql", "-uf21", "-pf21_local_demo_only", "-Df21_prep",
        "-N", "-B", "-e", sql,
    )
    return result.stdout.rstrip("\n")


def main():
    try:
        compose("up", "-d", "--build" if EXPECTED_STATUS == 200 else "--no-build", "mysql", "server")
        wait_for_health()
        token = login("student01")
        generate_key = str(uuid.uuid4())
        status, value = request(
            f"/students/{USER}/learning-path/generate",
            token,
            {"courseId": COURSE},
            "POST",
            generate_key,
        )
        assert status == 200, (status, value)
        path = value["data"]
        status, repeated = request(
            f"/students/{USER}/learning-path/generate", token,
            {"courseId": COURSE}, "POST", generate_key,
        )
        assert status == 200 and repeated["data"]["pathId"] == path["pathId"], repeated
        resource = next((node for node in path["nodes"] if node["itemType"] == "RESOURCE"), None)
        assert resource is not None, path
        node_id = resource["nodeId"]
        resource_id = resource["itemId"]

        status, viewed = request(
            f"/resources/{resource_id}/views",
            token,
            {"pathNodeId": node_id},
            "POST",
            str(uuid.uuid4()),
        )
        assert status == 200, (status, viewed)
        status, duplicate_view = request(
            f"/resources/{resource_id}/views", token,
            {"pathNodeId": node_id}, "POST", str(uuid.uuid4()),
        )
        if EXPECTED_STATUS == 200:
            assert status == 409, (status, duplicate_view)
        before_counts = mysql_query(
            "SELECT (SELECT COUNT(*) FROM mastery_state WHERE user_id='%s'), "
            "(SELECT COUNT(*) FROM mastery_history WHERE knowledge_id IN "
            "(SELECT knowledge_id FROM learning_path_node WHERE node_id='%s')), "
            "(SELECT COUNT(*) FROM learning_interaction WHERE user_id='%s')" % (USER, node_id, USER)
        ) if EXPECTED_STATUS == 200 else None

        status, completed = request(
            f"/resources/{resource_id}/completions",
            token,
            {"pathNodeId": node_id},
            "POST",
            str(uuid.uuid4()),
        )
        assert status == EXPECTED_STATUS, ("unexpected resource completion status", EXPECTED_STATUS, status, completed)
        if EXPECTED_STATUS == 500:
            message = completed.get("message", "")
            assert "服务暂时不可用" in message, completed
        else:
            assert completed.get("data", {}).get("activityType") == "COMPLETED", completed
            status, refreshed = request(
                f"/students/{USER}/learning-path?courseId={COURSE}", token,
            )
            assert status == 200, (status, refreshed)
            refreshed_node = next(node for node in refreshed["data"]["nodes"] if node["nodeId"] == node_id)
            assert refreshed_node["status"] == "COMPLETED", refreshed
            assert refreshed_node["sourceEventId"] == completed["data"]["sourceEventId"], (refreshed, completed)
            row = mysql_query(
                "SELECT COALESCE(source_event_id,''),COALESCE(source_resource_activity_id,'') "
                "FROM learning_path_node WHERE node_id='%s'" % node_id
            ).split("\t")
            assert row == ["", completed["data"]["sourceEventId"]], row
            after_counts = mysql_query(
                "SELECT (SELECT COUNT(*) FROM mastery_state WHERE user_id='%s'), "
                "(SELECT COUNT(*) FROM mastery_history WHERE knowledge_id IN "
                "(SELECT knowledge_id FROM learning_path_node WHERE node_id='%s')), "
                "(SELECT COUNT(*) FROM learning_interaction WHERE user_id='%s')" % (USER, node_id, USER)
            )
            assert after_counts == before_counts, (before_counts, after_counts)
        print(json.dumps({
            "reproduced": EXPECTED_STATUS == 500,
            "fixed": EXPECTED_STATUS == 200,
            "project": PROJECT,
            "serverPort": SERVER_PORT,
            "pathNodeId": node_id,
            "resourceId": resource_id,
            "completionStatus": status,
            "completionEventId": completed.get("data", {}).get("sourceEventId"),
            "traceId": completed.get("traceId"),
            "expectedDefect": "learning_path_node.source_event_id references event_consume_log while resource completion creates resource_activity.event_id",
        }, ensure_ascii=False, indent=2))
    finally:
        # Remove only this disposable validation stack; retain its volume.
        compose("down", check=False)


if __name__ == "__main__":
    main()

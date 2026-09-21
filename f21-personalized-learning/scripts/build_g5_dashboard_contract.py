#!/usr/bin/env python3
"""Build and independently validate the G5-07A dashboard contract; never trains or ranks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = Path("data/manifests/g5-dashboard-artifact-registry.json")
VALIDATION_PATH = Path("artifacts/g5-07a-contract-validation.json")
TRACE_ID = "2fc33dfb-dbd2-53e5-b6b3-2bbb1581f406"
ARTIFACT_PATH_PATTERN = r"^artifacts/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.json$"
G5_PATHS = (
    "/models", "/experiments", "/courses/{courseId}/weakness-statistics", "/courses/{courseId}/recommendation-metrics"
)
ROLE_MATRIX = {
    "/models": {"TEACHER": 200, "ADMIN": 200, "STUDENT": 403},
    "/experiments": {"TEACHER": 200, "ADMIN": 200, "STUDENT": 403},
    "/courses/{courseId}/weakness-statistics": {"SCOPED_TEACHER": 200, "ADMIN_ACTIVE": 200, "UNSCOPED_TEACHER": 403, "STUDENT": 403, "ADMIN_MISSING_OR_INACTIVE": 404},
    "/courses/{courseId}/recommendation-metrics": {"SCOPED_TEACHER": 200, "ADMIN_ACTIVE": 200, "UNSCOPED_TEACHER": 403, "STUDENT": 403, "ADMIN_MISSING_OR_INACTIVE": 404},
}
RECOMMENDATION_METRIC_FIELDS = {
    "availableItemRate": ("availableRecommendedItems", "recommendedItems"),
    "clickThroughRate": ("clicks", "exposures"),
    "helpfulRate": ("helpfulOpinions", "opinions"),
    "trustedCompletionRate": ("trustedCompletions", "exposures"),
}
FORBIDDEN_KEYS = {"password", "accesstoken", "answer", "answerjson", "studentid", "userid", "rawuserid"}
EXPECTED_ARTIFACTS = (
    ("g5-kt-comparison", "KT_COMPARISON", "PUBLIC_DATASET", "artifacts/g5-kt-comparison.json", "g5-kt-comparison-v2", "g5-04-report-validation", 10),
    ("g5-04-report-validation", "KT_VALIDATION", "PUBLIC_DATASET", "artifacts/g5-04-report-validation.json", "g5-04-report-validation-v1", None, 20),
    ("g5-recommendation-comparison", "RECOMMENDATION_COMPARISON", "PUBLIC_DATASET", "artifacts/g5-recommendation-comparison.json", "g5-recommendation-comparison-v1", "g5-05-report-validation", 30),
    ("g5-05-report-validation", "RECOMMENDATION_VALIDATION", "PUBLIC_DATASET", "artifacts/g5-05-report-validation.json", "g5-05-report-validation-v1", None, 40),
    ("g5-demo-catalog-validation", "DEMO_CATALOG_VALIDATION", "SYNTHETIC_DEMO", "artifacts/g5-demo-catalog-validation.json", "g5-demo-catalog-validation-v1", None, 50),
    ("g5-demo-catalog-import", "DEMO_CATALOG_IMPORT", "SYNTHETIC_DEMO", "artifacts/g5-demo-catalog-import.json", "g5-demo-catalog-import-v1", "g5-demo-catalog-validation", 60),
)
PROTECTED_TRACKED = {
    "artifacts/g5-kt-comparison.json": "5cb49d977f897110b6a0a3f9cf0d1a74d00355d082b61259857b254de8957e70",
    "artifacts/g5-04-report-validation.json": "5b077bea90a25a1b027f8983abb501b3a08a84844c80d36118c565dccaa372aa",
    "artifacts/g5-recommendation-comparison.json": "48c2b38d1fe88b0e2902c3fee2125b521e07bf667dc078fdb89b2958a257ab0e",
    "artifacts/g5-05-report-validation.json": "cb921d69c0bdef3174936dc3f4c11a06d49302fcaee641483dfcf16d6ac27a7e",
    "artifacts/g5-demo-catalog-validation.json": "5c043238cf1e54f073fdec9883825bf1321b667beedcf19b044ccc5bd172c4c6",
    "artifacts/g5-demo-catalog-import.json": "c617f680f2b51cc4ca3dcad86f89657f6b3f3f684e428b58651ab4e1579b14e1",
    "server/src/main/resources/db/migration/V1__Foundation_schema.sql": "c1574f8f574b5a3109a4b05ccea4e9fae128f61607f4d070940ed49a4189422e",
    "server/src/main/resources/db/migration/V3__Demo_catalog.sql": "f2fb89f5c5271aeb5b068bf46b9c341d59ed69c8aa3329427a503cd8c244f480",
    "server/src/main/resources/db/migration/V4__Practice_and_learning_state.sql": "cd19217b69564db201a8e66cde33b8273f9cf3d343bca683578949e96d9854b7",
    "server/src/main/resources/db/migration/V5__Recommendations_and_resource_activity.sql": "242ed78dc809e273e2f197bef387bf964010be5e0594df65d8b6761a1374c162",
    "server/src/main/resources/db/migration/V6__Learning_paths_catalog_import_and_recovery.sql": "5e17f12c662530787aabfa0af098bf567d45f50d12f061cb3b76a5504e01a1f7",
    "server/src/main/resources/db/migration/V7__G4_attribution_and_snapshot_order.sql": "f192449b75b4d75ee76bd6f3d23387b6fc0bb2edac4c410dad70b19623eb1bd3",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(root: Path, relative: str | Path) -> Any:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def is_safe_artifact_path(root: Path, relative: str) -> bool:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or re.fullmatch(ARTIFACT_PATH_PATTERN, relative) is None:
        return False
    try:
        return (root / candidate).resolve().is_relative_to((root / "artifacts").resolve()) and not (root / candidate).is_symlink()
    except FileNotFoundError:
        return False


def build_registry(root: Path = ROOT) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for artifact_id, artifact_type, data_type, relative, schema, validation_id, order in EXPECTED_ARTIFACTS:
        if not is_safe_artifact_path(root, relative):
            raise ValueError(f"unsafe artifact path: {relative}")
        payload = read_json(root, relative)
        if payload.get("passed") is not True or payload.get("schemaVersion") != schema:
            raise ValueError(f"unverified artifact: {relative}")
        artifacts.append({"artifactId": artifact_id, "artifactType": artifact_type, "dataType": data_type, "relativePath": relative, "schemaVersion": schema, "sha256": sha256(root / relative), "validationArtifactId": validation_id, "displayOrder": order, "publishStatus": "VERIFIED"})
    return {"schemaVersion": "g5-dashboard-artifact-registry-v1", "generator": "scripts/build_g5_dashboard_contract.py", "requestPolicy": {"trainsDuringRequest": False, "infersDuringRequest": False, "recomputesFormalTestDuringRequest": False}, "artifacts": artifacts}


def validate_registry(registry: dict[str, Any]) -> bool:
    expected = {row[0]: row for row in EXPECTED_ARTIFACTS}
    entries = registry.get("artifacts", [])
    if registry.get("schemaVersion") != "g5-dashboard-artifact-registry-v1" or len(entries) != len(expected):
        return False
    ids = [entry.get("artifactId") for entry in entries]
    if set(ids) != set(expected) or len(ids) != len(set(ids)):
        return False
    for entry in entries:
        artifact_id = entry["artifactId"]
        _, artifact_type, data_type, path, schema, validation_id, order = expected[artifact_id]
        if (entry.get("artifactType"), entry.get("dataType"), entry.get("relativePath"), entry.get("schemaVersion"), entry.get("validationArtifactId"), entry.get("displayOrder"), entry.get("publishStatus")) != (artifact_type, data_type, path, schema, validation_id, order, "VERIFIED"):
            return False
        if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
            return False
    return True


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def clean(root: Path) -> bool:
    return not git(root, "status", "--porcelain")


def git_tree_path(root: Path, relative: str) -> str:
    prefix = git(root, "rev-parse", "--show-prefix")
    return f"{prefix}{relative}"


def committed_bytes_match(root: Path, commit: str, relative: str) -> bool:
    try:
        return subprocess.check_output(["git", "show", f"{commit}:{git_tree_path(root, relative)}"], cwd=root) == (root / relative).read_bytes()
    except subprocess.CalledProcessError:
        return False


def fixtures_by_path(root: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fixture in read_json(root, "tests/fixtures/api-examples.json"):
        grouped.setdefault(fixture["path"], []).append(fixture)
    return grouped


def nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {key.lower() for key in value} | set().union(*(nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(nested_keys(item) for item in value)) if value else set()
    return set()


def expected_authorization(path: str) -> dict[str, Any]:
    authorization: dict[str, Any] = {
        "allowedRoles": ["TEACHER", "ADMIN"],
        "statusByActor": ROLE_MATRIX[path],
    }
    if path in G5_PATHS[2:]:
        authorization.update({
            "teacherScope": "ACTIVE_TEACHER_COURSE_SCOPE_REQUIRED",
            "courseStatus": "ACTIVE_REQUIRED",
        })
    return authorization


def valid_ratio(numerator: int, denominator: int, value: Any) -> bool:
    return value == (None if denominator == 0 else numerator / denominator)


def weakness_payloads_valid(normal: dict[str, Any], empty: dict[str, Any]) -> bool:
    population = normal.get("population", {})
    population_fields = {"enrolledLearners", "learnersWithState", "learnersWithEvidence", "eligibleLearners"}
    if set(population) != population_fields or set(empty.get("population", {})) != population_fields:
        return False
    ordered_population = (
        population.get("enrolledLearners"), population.get("learnersWithState"),
        population.get("learnersWithEvidence"), population.get("eligibleLearners"),
    )
    if normal.get("threshold") != {"minimumEvidenceCount": 3, "minimumEvidenceWeight": 2, "weaknessMasteryBelow": .6}:
        return False
    if not all(isinstance(value, int) for value in ordered_population) or not (ordered_population[0] >= ordered_population[1] >= ordered_population[2] >= ordered_population[3] >= 0):
        return False
    for item in normal.get("items", []):
        eligible = item.get("eligibleLearnerCount")
        weak = item.get("weakLearnerCount")
        mean = item.get("meanMastery")
        count = item.get("evidenceCount")
        weight = item.get("evidenceWeight")
        if not isinstance(eligible, int) or not isinstance(weak, int) or not 0 <= weak <= eligible <= population["eligibleLearners"]:
            return False
        if not valid_ratio(weak, eligible, item.get("weakLearnerRate")):
            return False
        if (eligible == 0 and mean is not None) or (eligible > 0 and (not isinstance(mean, (int, float)) or not 0 <= mean <= 1)):
            return False
        if not isinstance(count, int) or not isinstance(weight, (int, float)) or count < 3 * eligible or not 2 * eligible <= weight <= count:
            return False
    return normal.get("emptyReason") is None and empty.get("items") == [] and empty.get("emptyReason") == "NO_ELIGIBLE_EVIDENCE" and all(value == 0 for value in empty.get("population", {}).values())


def recommendation_payloads_valid(payloads: list[dict[str, Any]]) -> bool:
    population_fields = {"generatedBatches", "distinctLearners", "recommendedItems", "availableRecommendedItems", "exposures", "clicks", "opinions", "helpfulOpinions", "trustedCompletions"}
    if len(payloads) != 2:
        return False
    for payload in payloads:
        population = payload.get("population", {})
        metrics = payload.get("metrics", [])
        if set(population) != population_fields or not all(isinstance(value, int) and value >= 0 for value in population.values()):
            return False
        by_name = {metric.get("name"): metric for metric in metrics}
        if len(metrics) != len(by_name) or set(by_name) != set(RECOMMENDATION_METRIC_FIELDS):
            return False
        if not (population["availableRecommendedItems"] <= population["recommendedItems"] and population["clicks"] <= population["exposures"] <= population["recommendedItems"] and population["helpfulOpinions"] <= population["opinions"] <= population["recommendedItems"] and population["trustedCompletions"] <= population["exposures"]):
            return False
        for name, (numerator_field, denominator_field) in RECOMMENDATION_METRIC_FIELDS.items():
            metric = by_name[name]
            if metric.get("numerator") != population[numerator_field] or metric.get("denominator") != population[denominator_field] or not valid_ratio(metric["numerator"], metric["denominator"], metric.get("value")):
                return False
    empty = payloads[-1]
    return all(value == 0 for value in empty["population"].values()) and empty.get("emptyReason") == "NO_PRODUCT_EVENT_DATA"


def validate_dashboard_contract(root: Path = ROOT) -> dict[str, bool]:
    spec = read_json(root, "docs/openapi.json")
    fixtures = fixtures_by_path(root)
    paths = spec["paths"]
    operations = [paths[path]["get"] for path in G5_PATHS]
    required_responses = {"200", "400", "401", "403"}
    course_paths = set(G5_PATHS[2:])
    path_gate = all(path in paths and set(paths[path]) == {"get"} for path in G5_PATHS)
    operation_ids = [operation.get("operationId") for operation in operations]
    operation_gate = len(operation_ids) == len(set(operation_ids)) and all(operation.get("x-stage") == "G5" and operation.get("security") == [{"bearerAuth": []}] and required_responses <= set(operation.get("responses", {})) and (path not in course_paths or "404" in operation["responses"]) for path, operation in zip(G5_PATHS, operations))
    authorization_gate = all(paths[path]["get"].get("x-authorization") == expected_authorization(path) for path in G5_PATHS)
    model_experiment_gate = all(paths[path]["get"]["parameters"][0]["schema"]["minimum"] == 1 and paths[path]["get"]["parameters"][1]["schema"]["maximum"] == 100 for path in G5_PATHS[:2])
    fixture_gate = all({"g5-normal", "g5-empty", "g5-student-forbidden"} <= {item["name"] for item in fixtures.get(path, [])} for path in G5_PATHS) and all({"g5-unscoped-teacher-forbidden", "g5-admin-missing-or-inactive"} <= {item["name"] for item in fixtures.get(path, [])} for path in G5_PATHS[2:])
    g5_values = [item["value"] for path in G5_PATHS for item in fixtures.get(path, [])]
    sensitive_gate = not (nested_keys(g5_values) & FORBIDDEN_KEYS)
    artifact_schema = spec["components"]["schemas"]["ArtifactReference"]["properties"]["relativePath"]
    artifact_path_gate = artifact_schema.get("pattern") == ARTIFACT_PATH_PATTERN and all(re.fullmatch(artifact_schema["pattern"], value) is None for value in ("artifacts/../secret.json", "artifacts//secret.json", "/tmp/secret.json", "artifacts/nested/../../secret.json"))
    weakness = next(item["value"]["data"] for item in fixtures[G5_PATHS[2]] if item["name"] == "g5-normal")
    weakness_empty = next(item["value"]["data"] for item in fixtures[G5_PATHS[2]] if item["name"] == "g5-empty")
    weakness_gate = weakness_payloads_valid(weakness, weakness_empty)
    recommendation_sets = [item["value"]["data"] for item in fixtures[G5_PATHS[3]] if item["name"] in {"g5-normal", "g5-empty"}]
    recommendation_gate = recommendation_payloads_valid(recommendation_sets)
    registry = build_registry(root)
    registry_gate = validate_registry(registry)
    protected_gate = all((root / path).exists() and sha256(root / path) == digest for path, digest in PROTECTED_TRACKED.items())
    return {"paths": path_gate, "operations": operation_gate, "authorization": authorization_gate, "pagination": model_experiment_gate, "fixtures": fixture_gate, "artifactReferenceSchema": artifact_path_gate, "sensitiveFields": sensitive_gate, "weaknessMathAndEmpty": weakness_gate, "recommendationMathAndEmpty": recommendation_gate, "registry": registry_gate, "protectedTracked": protected_gate}


def ignored_evidence_gate(root: Path) -> bool:
    report = read_json(root, "artifacts/g5-recommendation-comparison.json")
    entries = [report["testEvaluationFile"], report["evaluationLedger"], report["testConsumption"], *report["rankingFiles"].values()]
    return all(is_safe_ignored(root, entry["path"], entry["sha256"]) for entry in entries)


def is_safe_ignored(root: Path, relative: str, digest: str) -> bool:
    path = root / relative
    return not Path(relative).is_absolute() and ".." not in Path(relative).parts and path.is_file() and sha256(path) == digest


def prepare(root: Path = ROOT) -> dict[str, Any]:
    gates = validate_dashboard_contract(root)
    if not all(gates.values()) or not ignored_evidence_gate(root):
        raise ValueError("dashboard contract preparation gate failed")
    registry = build_registry(root)
    write_json(root / REGISTRY_PATH, registry)
    return registry


def validate_frozen(expected_head: str, root: Path = ROOT) -> dict[str, Any]:
    if not clean(root) or git(root, "rev-parse", "HEAD") != expected_head:
        raise ValueError("validate-frozen requires the clean expected HEAD")
    required = ("docs/openapi.json", "tests/fixtures/api-examples.json", str(REGISTRY_PATH))
    if not all(committed_bytes_match(root, expected_head, relative) for relative in required):
        raise ValueError("contract input differs from expected Git tree")
    saved = read_json(root, REGISTRY_PATH)
    computed = build_registry(root)
    gates = validate_dashboard_contract(root)
    gates.update({"gitTree": True, "registryDeterministic": saved == computed, "ignoredFormalEvidence": ignored_evidence_gate(root)})
    if not all(gates.values()):
        raise ValueError("frozen dashboard contract gate failed")
    result = {"passed": True, "schemaVersion": "g5-07a-contract-validation-v2", "contractCommit": expected_head, "generator": {"script": "scripts/build_g5_dashboard_contract.py", "mode": "independent-validate-frozen"}, "manifest": {"path": str(REGISTRY_PATH), "sha256": sha256(root / REGISTRY_PATH), "artifactCount": len(saved["artifacts"])}, "registeredArtifactSha256": {item["artifactId"]: item["sha256"] for item in saved["artifacts"]}, "gates": gates, "commands": {"prepare": "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_g5_dashboard_contract.py prepare", "validateFrozen": "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_g5_dashboard_contract.py validate-frozen --expected-head <CONTRACT_COMMIT>"}, "environment": {"python": sys.version.split()[0], "platform": platform.platform()}, "requestPolicy": saved["requestPolicy"]}
    write_json(root / VALIDATION_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "validate-frozen"))
    parser.add_argument("--expected-head")
    args = parser.parse_args()
    if args.mode == "prepare":
        result = prepare()
    else:
        if not args.expected_head:
            parser.error("validate-frozen requires --expected-head")
        result = validate_frozen(args.expected_head)
    print(json.dumps({"passed": result.get("passed", True), "schemaVersion": result["schemaVersion"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts import build_g5_dashboard_contract as contract


class DashboardContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = contract.ROOT
        self.spec = json.loads((self.root / "docs/openapi.json").read_text(encoding="utf-8"))
        self.fixtures = json.loads((self.root / "tests/fixtures/api-examples.json").read_text(encoding="utf-8"))

    def schema_validator(self, name: str) -> Draft202012Validator:
        return Draft202012Validator({**self.spec["components"]["schemas"][name], "components": self.spec["components"]}, format_checker=FormatChecker())

    def g5_fixtures(self, path: str) -> list[dict]:
        return [fixture for fixture in self.fixtures if fixture["path"] == path and fixture["name"].startswith("g5-")]

    def test_all_paths_operations_security_and_responses_are_frozen(self) -> None:
        operations = []
        for path in contract.G5_PATHS:
            self.assertIn(path, self.spec["paths"])
            operation = self.spec["paths"][path]["get"]
            operations.append(operation)
            self.assertEqual("G5", operation["x-stage"])
            self.assertEqual([{"bearerAuth": []}], operation["security"])
            self.assertTrue({"200", "400", "401", "403"}.issubset(operation["responses"]))
        self.assertTrue({"404"}.issubset(self.spec["paths"][contract.G5_PATHS[2]]["get"]["responses"]))
        self.assertTrue({"404"}.issubset(self.spec["paths"][contract.G5_PATHS[3]]["get"]["responses"]))
        identifiers = [operation["operationId"] for operation in operations]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_openapi_version_is_g5_contract_version(self) -> None:
        self.assertEqual("0.6.0", self.spec["info"]["version"])

    def test_exact_role_matrix_is_frozen(self) -> None:
        for path in contract.G5_PATHS:
            self.assertEqual(contract.expected_authorization(path), self.spec["paths"][path]["get"]["x-authorization"])
        self.assertEqual(contract.ROLE_MATRIX["/models"], {"TEACHER": 200, "ADMIN": 200, "STUDENT": 403})
        self.assertEqual(contract.ROLE_MATRIX["/experiments"], {"TEACHER": 200, "ADMIN": 200, "STUDENT": 403})
        for path in contract.G5_PATHS[2:]:
            self.assertEqual(contract.ROLE_MATRIX[path], {"SCOPED_TEACHER": 200, "ADMIN_ACTIVE": 200, "UNSCOPED_TEACHER": 403, "STUDENT": 403, "ADMIN_MISSING_OR_INACTIVE": 404})
            names = {fixture["name"]: fixture["status"] for fixture in self.g5_fixtures(path)}
            self.assertEqual(403, names["g5-unscoped-teacher-forbidden"])
            self.assertEqual(404, names["g5-admin-missing-or-inactive"])

    def test_pagination_and_normal_empty_fixtures_validate(self) -> None:
        for path in contract.G5_PATHS[:2]:
            parameters = self.spec["paths"][path]["get"]["parameters"]
            self.assertEqual(1, parameters[0]["schema"]["minimum"])
            self.assertEqual(100, parameters[1]["schema"]["maximum"])
        for path in contract.G5_PATHS:
            names = {fixture["name"] for fixture in self.g5_fixtures(path)}
            self.assertTrue({"g5-normal", "g5-empty", "g5-student-forbidden"}.issubset(names))
            for fixture in self.g5_fixtures(path):
                self.schema_validator(fixture["schema"]).validate(fixture["value"])

    def test_statistical_denominators_and_empty_rules(self) -> None:
        gates = contract.validate_dashboard_contract(self.root)
        self.assertTrue(gates["weaknessMathAndEmpty"])
        self.assertTrue(gates["recommendationMathAndEmpty"])
        empty = next(f["value"]["data"] for f in self.g5_fixtures(contract.G5_PATHS[3]) if f["name"] == "g5-empty")
        self.assertTrue(all(metric["denominator"] == 0 and metric["value"] is None for metric in empty["metrics"]))

    def test_statistical_semantics_reject_count_and_population_mismatches(self) -> None:
        weakness = next(f["value"]["data"] for f in self.g5_fixtures(contract.G5_PATHS[2]) if f["name"] == "g5-normal")
        weakness_empty = next(f["value"]["data"] for f in self.g5_fixtures(contract.G5_PATHS[2]) if f["name"] == "g5-empty")
        invalid_weakness = copy.deepcopy(weakness)
        invalid_weakness["items"][0]["weakLearnerCount"] = invalid_weakness["items"][0]["eligibleLearnerCount"] + 1
        self.assertFalse(contract.weakness_payloads_valid(invalid_weakness, weakness_empty))
        recommendations = [f["value"]["data"] for f in self.g5_fixtures(contract.G5_PATHS[3]) if f["name"] in {"g5-normal", "g5-empty"}]
        invalid_mapping = copy.deepcopy(recommendations)
        invalid_mapping[0]["metrics"][1]["denominator"] = invalid_mapping[0]["population"]["recommendedItems"]
        invalid_mapping[0]["population"]["exposures"] -= 1
        self.assertFalse(contract.recommendation_payloads_valid(invalid_mapping))
        duplicate = copy.deepcopy(recommendations)
        duplicate[0]["metrics"][3]["name"] = "clickThroughRate"
        self.assertFalse(contract.recommendation_payloads_valid(duplicate))

    def test_artifact_schema_rejects_path_escape_and_rate_schemas_reject_out_of_range(self) -> None:
        artifact = {"relativePath": "artifacts/good.json", "sha256": "0" * 64, "schemaVersion": "v1", "checksumVerified": True}
        validator = self.schema_validator("ArtifactReference")
        validator.validate(artifact)
        for unsafe in ("artifacts/../secret.json", "artifacts//secret.json", "/tmp/secret.json", "artifacts/nested/../../secret.json"):
            invalid = {**artifact, "relativePath": unsafe}
            with self.assertRaises(ValidationError):
                validator.validate(invalid)
        recommendation = {"name": "clickThroughRate", "numerator": 2, "denominator": 1, "value": 2.0, "unit": "RATE"}
        with self.assertRaises(ValidationError):
            self.schema_validator("RecommendationMetric").validate(recommendation)
        recommendation_payload = copy.deepcopy(next(f["value"]["data"] for f in self.g5_fixtures(contract.G5_PATHS[3]) if f["name"] == "g5-normal"))
        recommendation_payload["metrics"][3]["name"] = "clickThroughRate"
        with self.assertRaises(ValidationError):
            self.schema_validator("RecommendationMetrics").validate(recommendation_payload)
        weakness_item = copy.deepcopy(next(f["value"]["data"]["items"][0] for f in self.g5_fixtures(contract.G5_PATHS[2]) if f["name"] == "g5-normal"))
        weakness_item["meanMastery"] = 1.01
        with self.assertRaises(ValidationError):
            self.schema_validator("WeaknessItem").validate(weakness_item)

    def test_sensitive_fields_are_excluded(self) -> None:
        values = [fixture["value"] for path in contract.G5_PATHS for fixture in self.g5_fixtures(path)]
        self.assertFalse(contract.nested_keys(values) & contract.FORBIDDEN_KEYS)

    def test_registry_is_allowlisted_hashed_and_deterministic(self) -> None:
        first = contract.build_registry(self.root)
        second = contract.build_registry(self.root)
        self.assertEqual(first, second)
        self.assertTrue(contract.validate_registry(first))
        self.assertEqual([entry["relativePath"] for entry in first["artifacts"]], [row[3] for row in contract.EXPECTED_ARTIFACTS])
        for entry in first["artifacts"]:
            self.assertEqual(contract.sha256(self.root / entry["relativePath"]), entry["sha256"])

    def test_registry_rejects_validation_binding_mismatch_and_unverified_artifact(self) -> None:
        registry = contract.build_registry(self.root)
        invalid = copy.deepcopy(registry)
        invalid["artifacts"][0]["validationArtifactId"] = None
        self.assertFalse(contract.validate_registry(invalid))
        with patch.object(contract, "read_json", return_value={"passed": False, "schemaVersion": "g5-kt-comparison-v2"}):
            with self.assertRaisesRegex(ValueError, "unverified"):
                contract.build_registry(self.root)

    def test_path_traversal_and_symlink_escape_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifacts").mkdir()
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            (root / "artifacts/link.json").symlink_to(outside)
            self.assertFalse(contract.is_safe_artifact_path(root, "../outside.json"))
            self.assertFalse(contract.is_safe_artifact_path(root, "/tmp/outside.json"))
            self.assertFalse(contract.is_safe_artifact_path(root, "artifacts/link.json"))

    def test_protected_artifacts_migrations_and_ignored_formal_evidence_are_unchanged(self) -> None:
        gates = contract.validate_dashboard_contract(self.root)
        self.assertTrue(gates["protectedTracked"])
        self.assertTrue(contract.ignored_evidence_gate(self.root))


if __name__ == "__main__":
    unittest.main()

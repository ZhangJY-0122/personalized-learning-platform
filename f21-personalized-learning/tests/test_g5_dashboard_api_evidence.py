import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/g5-07b-api-validation.json"
REGISTRY = ROOT / "data/manifests/g5-dashboard-artifact-registry.json"


class G5DashboardApiEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads(ARTIFACT.read_text())

    def test_frozen_evidence_is_complete_and_passed(self):
        self.assertEqual("g5-07b-api-validation-v1", self.evidence["schemaVersion"])
        self.assertTrue(self.evidence["passed"])
        self.assertEqual([], self.evidence["failures"])
        self.assertEqual(15, len(self.evidence["checks"]))
        self.assertTrue(all(self.evidence["checks"].values()))
        self.assertEqual(
            {"successfulContractResponses": 4, "boundaryResponses": 8},
            self.evidence["testCounts"],
        )

    def test_evidence_binds_frozen_contract_registry_and_implementation(self):
        expected_hash = hashlib.sha256(REGISTRY.read_bytes()).hexdigest()
        self.assertEqual(expected_hash, self.evidence["registry"]["sha256"])
        self.assertEqual(6, self.evidence["registry"]["artifactCount"])
        self.assertTrue(self.evidence["registry"]["checksumsVerified"])
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", self.evidence["contractCommit"], self.evidence["implementationCommit"]],
            cwd=ROOT,
            check=True,
        )
        committed_runner = subprocess.run(
            ["git", "show", f"{self.evidence['implementationCommit']}:f21-personalized-learning/tests/g5_dashboard_api_live.py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual((ROOT / "tests/g5_dashboard_api_live.py").read_bytes(), committed_runner)

    def test_evidence_contains_no_runtime_secrets_or_raw_payloads(self):
        self.assertEqual(
            {"credentialsRecorded": False, "rawPayloadsRecorded": False, "tokensRecorded": False},
            self.evidence["security"],
        )
        serialized = ARTIFACT.read_text()
        self.assertNotIn("Learn@", serialized)
        self.assertNotIn("accessToken", serialized)
        self.assertFalse(any(self.evidence["requestPolicy"].values()))


if __name__ == "__main__":
    unittest.main()

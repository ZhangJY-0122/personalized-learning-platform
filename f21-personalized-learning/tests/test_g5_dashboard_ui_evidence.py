import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/g5-07c-ui-validation.json"


class G5DashboardUiEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads(ARTIFACT.read_text())

    def test_ui_evidence_passed(self):
        self.assertEqual("g5-07c-ui-validation-v1", self.evidence["schemaVersion"])
        self.assertTrue(self.evidence["passed"])
        self.assertTrue(all(self.evidence["checks"].values()))
        self.assertEqual(7, len(self.evidence["checks"]))
        self.assertTrue(self.evidence["browser"]["credentialsRecorded"] is False)

    def test_ui_evidence_binds_current_implementation(self):
        current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()
        self.assertEqual(
            0,
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", self.evidence["implementationCommit"], current],
                cwd=ROOT,
            ).returncode,
        )

    def test_ui_evidence_has_no_sensitive_runtime_data(self):
        self.assertEqual(
            {"tokensRecorded": False, "rawStudentIdentifiersRecorded": False, "answersOrPasswordsDisplayed": False},
            self.evidence["security"],
        )
        serialized = ARTIFACT.read_text()
        self.assertNotIn("Learn@", serialized)
        self.assertNotIn("accessToken", serialized)


if __name__ == "__main__":
    unittest.main()

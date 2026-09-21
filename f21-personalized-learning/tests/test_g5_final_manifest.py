import unittest

from scripts.build_g5_final_manifest import REQUIRED_ARTIFACTS, build_manifest


class G5FinalManifestTest(unittest.TestCase):
    def test_final_manifest_closes_required_scope(self):
        manifest = build_manifest(source_commit="test-commit", verified_at="2026-09-20T00:00:00Z")
        self.assertTrue(manifest["passed"])
        self.assertEqual(manifest["releaseStatus"], "READY_FOR_MAIN_MERGE")
        self.assertEqual(len(manifest["evidence"]), len(REQUIRED_ARTIFACTS))
        self.assertEqual(manifest["verification"]["g4ScenariosPassed"], 38)
        self.assertEqual(manifest["verification"]["g5CoursesExercised"], 3)
        self.assertGreaterEqual(manifest["verification"]["g5FreshVolumeHttpChecks"], 70)

    def test_optional_scope_is_explicitly_excluded(self):
        manifest = build_manifest(source_commit="test-commit", verified_at="2026-09-20T00:00:00Z")
        excluded = set(manifest["excludedOptionalScope"])
        self.assertIn("online DKT serving", excluded)
        self.assertIn("mobile-specific delivery", excluded)
        self.assertTrue(manifest["dataBoundary"]["formalExperimentsAreOfflineOnly"])


if __name__ == "__main__":
    unittest.main()

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_g5_data_license import validate_manifest
MANIFEST_PATH = ROOT / "data/manifests/assistments-2009-2010-corrected-license.json"


class G5DataLicenseGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def assert_rejected(self, mutate, expected):
        candidate = copy.deepcopy(self.manifest)
        mutate(candidate)
        errors = validate_manifest(candidate)
        self.assertTrue(any(expected in error for error in errors), errors)

    def test_valid_manifest_passes(self):
        self.assertEqual(validate_manifest(self.manifest), [])

    def test_missing_official_data_page_fails(self):
        self.assert_rejected(lambda m: m.pop("officialPage"), "officialPage")

    def test_missing_terms_page_fails(self):
        self.assert_rejected(lambda m: m.pop("termsPage"), "termsPage")

    def test_spdx_license_claim_fails(self):
        self.assert_rejected(lambda m: m.update(standardLicense="CC-BY-4.0"), "standardLicense")

    def test_redistribution_allowed_fails(self):
        self.assert_rejected(lambda m: m.update(redistributionAllowed=True), "redistributionAllowed")

    def test_split_multi_skill_policy_fails(self):
        self.assert_rejected(lambda m: m.update(multiSkillPolicy="SPLIT_UNDERSCORE"), "multiSkillPolicy")

    def test_raw_download_fails(self):
        self.assert_rejected(lambda m: m.update(rawDataDownloaded=True), "rawDataDownloaded")


if __name__ == "__main__":
    unittest.main()

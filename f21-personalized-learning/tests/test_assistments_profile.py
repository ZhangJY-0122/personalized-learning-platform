import csv
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from scripts.profile_assistments import profile


FIELDS = ["order_id", "user_id", "problem_id", "correct", "skill_id", "answer_text"]


def write_rows(path: Path, rows, *, encoding="utf-8"):
    with path.open("w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(rows)


class AssistmentsProfileTest(unittest.TestCase):
    def test_composite_skill_token_is_not_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            write_rows(path, [["10", "7", "11", "1", "1_13", "ok"]])
            result = profile(path)
            self.assertEqual(result["uniqueCounts"]["skills"], 1)
            self.assertEqual(result["multiSkillCompositeTokenRows"], 1)
            self.assertEqual(result["multiSkillPolicy"], "COMPOSITE_TOKEN_NO_SPLIT")

    def test_exact_duplicate_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            row = ["10", "7", "11", "1", "1_13", "ok"]
            write_rows(path, [row, row])
            result = profile(path)
            self.assertEqual(result["duplicates"]["duplicateRows"], 1)
            self.assertEqual(result["duplicates"]["conflictingRows"], 0)
            self.assertTrue(result["passed"])

    def test_conflicting_event_key_fails_without_private_error_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            write_rows(path, [
                ["10", "12345", "98765", "1", "1_13", "private-answer"],
                ["10", "12345", "98765", "0", "1_13", "other-answer"],
            ])
            result = profile(path)
            self.assertEqual(result["duplicates"]["conflictingRows"], 1)
            self.assertFalse(result["passed"])
            self.assertTrue(any("conflict" in error for error in result["errors"]))
            self.assertFalse(any(token in " ".join(result["errors"]) for token in ("12345", "98765", "private-answer", "other-answer")))

    def test_malformed_csv_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            path.write_text("order_id,user_id,problem_id,correct,skill_id,answer_text\n10,7,11,1,1_13\n", encoding="utf-8")
            result = profile(path)
            self.assertFalse(result["passed"])

    def test_missing_core_or_invalid_correct_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            write_rows(path, [["", "7", "11", "1", "1", "ok"], ["12", "7", "13", "2", "1", "ok"]])
            result = profile(path)
            self.assertFalse(result["passed"])

    def test_utf8_encoding_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            write_rows(path, [["10", "7", "11", "1", "1", "中文"]])
            result = profile(path)
            self.assertEqual(result["sourceEncoding"], "utf-8")
            self.assertEqual(result["utf8DecodeErrorCount"], 0)
            self.assertFalse(result["encodingFallbackUsed"])

    def test_windows1252_fallback_has_no_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            write_rows(path, [["10", "7", "11", "1", "1", "Euro €"]], encoding="windows-1252")
            result = profile(path)
            self.assertEqual(result["sourceEncoding"], "windows-1252")
            self.assertGreater(result["utf8DecodeErrorCount"], 0)
            self.assertEqual(result["replacementCharacterCount"], 0)
            self.assertTrue(result["encodingFallbackUsed"])
            self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()

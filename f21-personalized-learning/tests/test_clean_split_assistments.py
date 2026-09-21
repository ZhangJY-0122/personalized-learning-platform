import csv
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.clean_split_assistments import (
    build_splits,
    clean_rows,
    order_key,
    public_row,
    split_counts,
    validate_split_invariants,
)


FIELDS = ["order_id", "user_id", "problem_id", "correct", "skill_id", "answer_id"]


class CleanSplitTest(unittest.TestCase):
    def write(self, path, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(FIELDS)
            writer.writerows(rows)

    def test_cleaning_anonymizes_and_preserves_composite_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.csv"
            self.write(path, [["2", "student-a", "item-b", "0", "1_13", ""], ["1", "student-a", "item-a", "1", "2", "42"]])
            rows, report, maps = clean_rows(path)
            self.assertEqual(report["retainedRows"], 2)
            self.assertEqual(rows[0]["orderId"], "1")
            self.assertEqual(rows[1]["skillToken"], "1_13")
            self.assertNotEqual(rows[0]["studentExternalId"], "student-a")
            self.assertEqual(len(maps["student"]), 1)

    def test_numeric_problem_order_wins_over_anonymous_hash_and_outputs_hide_raw_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.csv"
            # Numeric 2 < 100, while the domain-separated item hashes sort 100 before 2.
            self.write(path, [["7", "student-raw", "100", "1", "1", ""], ["7", "student-raw", "2", "0", "1", ""]])
            rows, _, _ = clean_rows(path)
            self.assertEqual([row["sourceRow"] for row in rows], [3, 2])
            serialized = json.dumps([public_row(row) for row in rows], ensure_ascii=False)
            self.assertNotIn("student-raw", serialized)
            self.assertNotIn('"100"', serialized)
            self.assertNotIn('"2"', serialized)

    def test_exact_duplicates_are_filtered_and_conflicts_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.csv"
            self.write(path, [["1", "s", "p", "1", "1", ""], ["1", "s", "p", "1", "1", ""], ["1", "s", "p", "0", "1", ""]])
            rows, report, _ = clean_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(report["duplicateRowsFiltered"], 1)
            self.assertEqual(report["conflictingRows"], 1)

    def sample_rows(self, count=5, student="a"):
        return [{"studentExternalId": student, "orderId": str(i), "itemExternalId": f"item-{i}", "_problemSort": order_key(str(i)), "sourceRow": i + 2} for i in range(count)]

    def test_split_guarantees_three_one_one_and_short_coverage(self):
        rows = self.sample_rows(5) + self.sample_rows(4, "b")
        assignments, report = build_splits(rows)
        self.assertEqual(split_counts(5), (3, 1, 1))
        self.assertEqual(report["eligibleStudents"], 1)
        self.assertEqual(report["shortStudents"], 1)
        self.assertEqual(report["partitionRows"]["train"], 3)
        self.assertEqual(report["partitionRows"]["validation"], 1)
        self.assertEqual(report["partitionRows"]["test"], 1)
        self.assertEqual(report["partitionRows"]["SHORT_COVERAGE"], 4)
        self.assertEqual(len(assignments), 9)

    def legal_case(self):
        rows = self.sample_rows(6)
        assignments, _ = build_splits(rows)
        return rows, assignments

    def test_all_split_invariants_pass_for_legal_case(self):
        rows, assignments = self.legal_case()
        result = validate_split_invariants(rows, assignments, [])
        self.assertTrue(result["passed"])
        self.assertTrue(all(result[name] for name in ("assignedExactlyOnce", "coverageComplete", "partitionsDisjoint", "sequenceIndexContiguous", "partitionOrderValid", "shortCoverageIsolated", "sourceOrderPreserved")))

    def test_duplicate_assignment_fails(self):
        rows, assignments = self.legal_case()
        assignments.append(dict(assignments[0]))
        self.assertFalse(validate_split_invariants(rows, assignments, [])['assignedExactlyOnce'])

    def test_missing_assignment_fails(self):
        rows, assignments = self.legal_case()
        assignments.pop()
        self.assertFalse(validate_split_invariants(rows, assignments, [])['coverageComplete'])

    def test_interleaved_partition_fails(self):
        rows, assignments = self.legal_case()
        assignments[0]["partition"], assignments[4]["partition"] = assignments[4]["partition"], assignments[0]["partition"]
        self.assertFalse(validate_split_invariants(rows, assignments, [])['partitionOrderValid'])

    def test_noncontiguous_sequence_index_fails(self):
        rows, assignments = self.legal_case()
        assignments[1]["sequenceIndex"] = 9
        self.assertFalse(validate_split_invariants(rows, assignments, [])['sequenceIndexContiguous'])

    def test_short_sequence_mixed_into_main_partition_fails(self):
        rows = self.sample_rows(4)
        assignments, _ = build_splits(rows)
        assignments[0]["partition"] = "train"
        self.assertFalse(validate_split_invariants(rows, assignments, [])['shortCoverageIsolated'])

    def test_processed_outputs_must_be_ignored(self):
        rows, assignments = self.legal_case()
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / name for name in ("clean.jsonl", "map.json", "split.jsonl")]
            result = validate_split_invariants(rows, assignments, paths)
            self.assertFalse(result["processedFilesIgnored"])
            self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()

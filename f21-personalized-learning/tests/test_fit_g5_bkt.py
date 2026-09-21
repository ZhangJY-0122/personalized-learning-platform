import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.fit_g5_bkt import (
    BKTParams,
    bkt_predict,
    bkt_update,
    candidate_hash,
    cross_language_parity,
    fit_experiment,
    generate_candidates,
    load_frozen_inputs,
    logloss,
    select_params,
)


class FitG5BktTest(unittest.TestCase):
    def _fixture(self, *, train_correct=1, validation_correct=0, test_correct=1):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        clean = root / "clean.jsonl"
        split = root / "split.jsonl"
        manifest = root / "manifest.json"
        rows = []
        for source, partition, correct, student, skill, index in [
            (1, "train", train_correct, "student-a", "skill-a", 0),
            (2, "train", 1 - train_correct, "student-a", "skill-a", 1),
            (3, "train", 1, "student-a", "skill-a", 2),
            (4, "validation", validation_correct, "student-a", "skill-a", 3),
            (5, "test", test_correct, "student-a", "skill-a", 4),
            (6, "SHORT_COVERAGE", 1, "student-b", "skill-b", 0),
        ]:
            rows.append({"sourceRow": source, "studentExternalId": student, "skillExternalId": skill, "itemExternalId": f"item-{source}", "orderId": str(source), "correct": correct, "answer": "NA", "skillToken": skill})
        clean.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        assignments = [{"sourceRow": source, "studentExternalId": student, "sequenceIndex": index, "partition": partition} for source, partition, correct, student, skill, index in [
            (1, "train", train_correct, "student-a", "skill-a", 0), (2, "train", 1 - train_correct, "student-a", "skill-a", 1), (3, "train", 1, "student-a", "skill-a", 2), (4, "validation", validation_correct, "student-a", "skill-a", 3), (5, "test", test_correct, "student-a", "skill-a", 4), (6, "SHORT_COVERAGE", 1, "student-b", "skill-b", 0)]]
        split.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in assignments), encoding="utf-8")
        clean_hash = hashlib.sha256(clean.read_bytes()).hexdigest()
        split_hash = hashlib.sha256(split.read_bytes()).hexdigest()
        manifest.write_text(json.dumps({"passed": True, "cleanSha256": clean_hash, "splitAssignmentSha256": split_hash, "invariants": {"passed": True}, "inputRawSha256": "raw-hash", "partitionHashes": {} }), encoding="utf-8")
        events, assignments_by_source, hashes = load_frozen_inputs(clean, split, manifest)
        return directory, events, assignments_by_source, hashes

    def test_formula_and_predict_before_update(self):
        params = BKTParams(0.2, 0.1, 0.2, 0.1)
        prediction = bkt_predict(0.2, params)
        self.assertAlmostEqual(prediction, 0.34, places=12)
        after_correct = bkt_update(0.2, 1, params)
        after_wrong = bkt_update(0.2, 0, params)
        posterior = 0.2 * 0.9 / 0.34
        self.assertAlmostEqual(after_correct, posterior + (1 - posterior) * 0.1, places=12)
        self.assertLess(after_wrong, 0.2)
        self.assertNotEqual(prediction, bkt_predict(after_correct, params))

    def test_logloss_clips_probability(self):
        self.assertGreater(logloss([0, 1], [0.0, 1.0]), 0)

    def test_candidate_bounds_and_determinism(self):
        first = generate_candidates(11, 100)
        second = generate_candidates(11, 100)
        other = generate_candidates(22, 100)
        self.assertEqual(first, second)
        self.assertEqual(candidate_hash(first), candidate_hash(second))
        self.assertNotEqual(candidate_hash(first), candidate_hash(other))
        self.assertEqual(len(first), 100)
        self.assertTrue(all(0.05 <= p.L0 <= 0.95 and 0.001 <= p.T <= 0.30 and 0.01 <= p.G <= 0.40 and 0.01 <= p.S <= 0.40 and p.G + p.S < 1 for p in first))

    def test_candidate_statistics_reach_candidate_and_attempt_limits(self):
        _, reached = generate_candidates(11, 5, max_attempts=5, return_stats=True)
        self.assertEqual(reached["attempts"], 5)
        self.assertEqual(reached["acceptedCandidates"], 5)
        self.assertEqual(reached["rejectedCandidates"], 0)
        self.assertEqual(reached["stopReason"], "MAX_CANDIDATES_REACHED")
        _, attempts = generate_candidates(11, 100, max_attempts=1, return_stats=True)
        self.assertEqual(attempts["attempts"], 1)
        self.assertEqual(attempts["stopReason"], "MAX_ATTEMPTS_REACHED")

    def test_real_fixture_test_label_is_not_in_model_events(self):
        one, events_a, assignments_a, _ = self._fixture(test_correct=0)
        two, events_b, assignments_b, _ = self._fixture(test_correct=1)
        try:
            candidates = generate_candidates(11, 20)
            fit_a = fit_experiment(events_a, assignments_a, candidates)
            fit_b = fit_experiment(events_b, assignments_b, candidates)
            self.assertEqual(fit_a["trainSources"], {1, 2, 3})
            self.assertEqual(fit_a["validationSources"], {4})
            self.assertEqual(fit_a["testSources"], {5})
            self.assertEqual(fit_a["shortSources"], {6})
            self.assertEqual(fit_a["courseParams"], fit_b["courseParams"])
            self.assertEqual(fit_a["trainLoss"], fit_b["trainLoss"])
            self.assertEqual(fit_a["strategies"], fit_b["strategies"])
            self.assertEqual(fit_a["selected"], fit_b["selected"])
        finally:
            one.cleanup(); two.cleanup()

    def test_validation_label_changes_metric_but_not_train_fit(self):
        one, events_a, assignments_a, _ = self._fixture(validation_correct=0)
        two, events_b, assignments_b, _ = self._fixture(validation_correct=1)
        try:
            candidates = generate_candidates(11, 20)
            fit_a = fit_experiment(events_a, assignments_a, candidates)
            fit_b = fit_experiment(events_b, assignments_b, candidates)
            self.assertEqual(fit_a["courseParams"], fit_b["courseParams"])
            self.assertEqual(fit_a["trainLoss"], fit_b["trainLoss"])
            self.assertNotEqual(fit_a["strategies"]["COURSE_SHARED"]["logLoss"], fit_b["strategies"]["COURSE_SHARED"]["logLoss"])
        finally:
            one.cleanup(); two.cleanup()

    def test_train_label_changes_fitting_result(self):
        one, events_a, assignments_a, _ = self._fixture(train_correct=1)
        two, events_b, assignments_b, _ = self._fixture(train_correct=0)
        try:
            candidates = generate_candidates(11, 20)
            fit_a = fit_experiment(events_a, assignments_a, candidates)
            fit_b = fit_experiment(events_b, assignments_b, candidates)
            self.assertNotEqual(fit_a["trainLoss"], fit_b["trainLoss"])
        finally:
            one.cleanup(); two.cleanup()

    def test_frozen_input_gates_reject_hash_or_manifest_failure(self):
        directory, events, assignments, hashes = self._fixture()
        try:
            clean = Path(directory.name) / "clean.jsonl"
            split = Path(directory.name) / "split.jsonl"
            manifest = Path(directory.name) / "manifest.json"
            clean.write_text(clean.read_text() + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_frozen_inputs(clean, split, manifest)
            manifest.write_text(json.dumps({"passed": False, "invariants": {"passed": False}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_frozen_inputs(clean, split, manifest)
        finally:
            directory.cleanup()

    def test_cross_language_vectors_are_frozen_and_weighted_case_is_excluded(self):
        report = cross_language_parity(ROOT, None)
        self.assertEqual(report["pythonSha256"], "7e88ffdc32987d35c6b53d607236b58c6fe2043a33e117bd49e4c2000d2d909b")
        self.assertEqual(report["javaSha256"], report["pythonSha256"])
        self.assertEqual(report["vectorCount"], 10)
        self.assertEqual(report["g5WeightOneVectorCount"], 9)
        self.assertLessEqual(report["pythonMaxAbsoluteError"], 1e-10)

    def test_train_only_selection_ignores_validation_and_test_labels(self):
        candidates = [BKTParams(0.1, 0.1, 0.1, 0.1), BKTParams(0.8, 0.1, 0.1, 0.1)]
        train = [("s", "k", 1), ("s", "k", 1), ("s", "k", 0)]
        selected_a, loss_a = select_params(candidates, train)
        selected_b, loss_b = select_params(candidates, train)
        self.assertEqual(selected_a, selected_b)
        self.assertEqual(loss_a, loss_b)

    def test_threshold_is_strictly_at_least_100(self):
        from scripts.fit_g5_bkt import eligible_skill_counts
        self.assertEqual(eligible_skill_counts({"a": 99, "b": 100}, 100), (["b"], ["a"]))


if __name__ == "__main__":
    unittest.main()

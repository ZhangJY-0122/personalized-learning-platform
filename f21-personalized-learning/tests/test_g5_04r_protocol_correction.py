import unittest
from unittest.mock import patch

import torch

from training.g5_04r_correction import (
    build_corrective_entry,
    corrective_consumed,
    invalidate_prior_seed33,
    ledger_counts,
    state_hash,
    validate_prediction_rows,
)
from training import run_g5_kt_comparison as kt


class G5ProtocolCorrectionTest(unittest.TestCase):
    def test_fixed_epoch_training_does_not_restore_validation_best(self):
        model = kt.DKT(3, 4)
        validation = iter([0.60, 0.50, 0.55, 0.56, 0.57, 0.58, 0.59, 0.60])

        def fake_predict(_model, _examples):
            loss = next(validation)
            # Two labels with probabilities whose log loss is deterministic.
            return [1, 0], [1.0 - loss / 2, loss / 2], [1, 2]

        with patch.object(kt, "run_epoch", return_value=0.4), patch.object(kt, "predict_examples", side_effect=fake_predict):
            result = kt.train_model(model, [], [{}], {"learningRate": 0.001}, 11, epochs=8, select_best=False)
        self.assertEqual(result["actualEpochs"], 8)
        self.assertEqual(result["savedEpoch"], 8)
        self.assertEqual(result["bestEpoch"], 2)

    def test_state_hash_is_ordered_and_sensitive(self):
        a = {"b": torch.tensor([2.0]), "a": torch.tensor([1.0])}
        b = {"a": torch.tensor([1.0]), "b": torch.tensor([2.0])}
        self.assertEqual(state_hash(a), state_hash(b))
        b["b"][0] = 3.0
        self.assertNotEqual(state_hash(a), state_hash(b))

    def test_running_entry_is_built_before_inference_and_prior_seed_is_invalidated(self):
        candidate = {"sha256": "file", "stateSha256": "state"}
        entry = build_corrective_entry(candidate, "targets")
        self.assertEqual(entry["status"], "running")
        self.assertTrue(entry["started"])
        self.assertFalse(entry["completed"])
        attempts = [{"model": "DKT", "seed": 33, "status": "completed", "completed": True}]
        invalidate_prior_seed33(attempts)
        self.assertEqual(attempts[0]["status"], "invalidated")
        self.assertFalse(attempts[0]["completed"])

    def test_old_completed_seed33_is_not_a_completed_corrective_attempt(self):
        old = {"model": "DKT", "seed": 33, "status": "completed"}
        corrective = {"model": "DKT", "seed": 33, "status": "completed", "corrective": True}
        self.assertFalse(old.get("corrective", False))
        self.assertTrue(corrective["corrective"])

    def test_prediction_and_ledger_gates(self):
        expected = {1: 1, 2: 0}
        self.assertEqual(validate_prediction_rows([{"sourceRow": 1, "label": 1, "prediction": 0.8}, {"sourceRow": 2, "label": 0, "prediction": 0.2}], expected), [])
        self.assertTrue(validate_prediction_rows([{"sourceRow": 1, "label": 0, "prediction": 0.8}], expected))
        counts = ledger_counts([
            {"model": "RULE", "status": "completed"}, {"model": "BKT", "status": "completed"},
            {"model": "DKT", "seed": 11, "status": "completed"}, {"model": "DKT", "seed": 22, "status": "completed"},
            {"model": "DKT", "seed": 33, "status": "invalidated"}, {"model": "DKT", "seed": 33, "status": "completed"},
        ])
        self.assertEqual(counts, {"RULE": 1, "BKT": 1, "DKT": {"11": 1, "22": 1, "33": 1}, "invalidatedDKT33": 1})

    def test_any_corrective_status_is_permanently_consumed(self):
        for status in ("running", "completed", "failed", "invalidated"):
            self.assertTrue(corrective_consumed([{"corrective": True, "status": status}]))

    def test_prediction_gate_rejects_duplicate_missing_extra_replaced_and_invalid_probability(self):
        expected = {1: 1, 2: 0}
        cases = [
            ([{"sourceRow": 1, "label": 1, "prediction": 0.8}, {"sourceRow": 1, "label": 1, "prediction": 0.8}], "target set mismatch"),
            ([{"sourceRow": 1, "label": 1, "prediction": 0.8}], "target set mismatch"),
            ([{"sourceRow": 1, "label": 1, "prediction": 0.8}, {"sourceRow": 3, "label": 0, "prediction": 0.2}], "target set mismatch"),
            ([{"sourceRow": 1, "label": 0, "prediction": 0.8}, {"sourceRow": 2, "label": 0, "prediction": 0.2}], "label mismatch"),
            ([{"sourceRow": 1, "label": 1, "prediction": float("nan")}, {"sourceRow": 2, "label": 0, "prediction": 0.2}], "invalid probability"),
        ]
        for rows, expected_error in cases:
            self.assertIn(expected_error, validate_prediction_rows(rows, expected))


if __name__ == "__main__":
    unittest.main()

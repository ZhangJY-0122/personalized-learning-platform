from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from training.run_g5_recommendation_comparison import (
    Interaction,
    MetricAccumulator,
    ScanInfo,
    UserCut,
    build_cut_users,
    build_model,
    build_validation_users,
    candidate_scores,
    committed_file_matches,
    evaluate_fixed_hybrid,
    evaluate_users,
    freeze_report,
    minmax,
    select_weights,
    top_items,
    update_mastery,
    validate_frozen,
    weight_grid,
    evaluate_once,
)
from scripts.fit_g5_bkt import BKTParams, bkt_update


class RecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.params = self.root / "params.json"
        values = {"L0": 0.5, "T": 0.1, "G": 0.1, "S": 0.1}
        self.params.write_text(json.dumps({"schemaVersion": "g5-bkt-fit-v1", "courseShared": values, "skillSpecific": {"skill_a": values, "skill_b": values, "skill_1_2": values}}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def paths(self) -> dict[str, Path]:
        return {"bktParameters": self.params}

    def test_committed_file_matches_from_nested_project_directory(self) -> None:
        repository = self.root / "repository"
        project = repository / "nested-project"
        artifact = project / "artifacts/evidence.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"passed":true}\n', encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "G5 Test"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "g5-test@example.invalid"], cwd=repository, check=True)
        subprocess.run(["git", "add", "nested-project/artifacts/evidence.json"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "test evidence"], cwd=repository, check=True)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()

        self.assertTrue(committed_file_matches(project, commit, "artifacts/evidence.json"))
        artifact.write_text('{"passed":false}\n', encoding="utf-8")
        self.assertFalse(committed_file_matches(project, commit, "artifacts/evidence.json"))

    def model(self, rows: list[Interaction]):
        return build_model(rows, self.paths())

    def test_train_only_candidate_directory_and_global_statistics(self) -> None:
        train = [Interaction(1, "s", "b", "skill_b", 0, "train", 0), Interaction(2, "s", "a", "skill_a", 1, "train", 1)]
        model = self.model(train)
        self.assertEqual(model.items, ["a", "b"])
        self.assertEqual(model.popularity.tolist(), [1.0, 1.0])
        with_future = self.model(train + [Interaction(3, "s", "z", "skill_a", 1, "validation", 2), Interaction(4, "s", "y", "skill_b", 1, "test", 3)])
        self.assertEqual(with_future.items, ["a", "b", "y", "z"])
        self.assertEqual(model.items, ["a", "b"])

    def test_correctness_does_not_change_relevance(self) -> None:
        train = [Interaction(1, "s", "a", "skill_a", 0, "train", 0)]
        model = self.model(train)
        first = build_validation_users(train, [Interaction(2, "s", "b", "skill_b", 0, "validation", 1)], model)[0]
        second = build_validation_users(train, [Interaction(2, "s", "b", "skill_b", 1, "validation", 1)], model)[0]
        self.assertEqual(first.relevant_items, second.relevant_items)

    def test_composite_skill_token_is_atomic(self) -> None:
        model = self.model([Interaction(1, "s", "a", "skill_1_2", 1, "train", 0)])
        self.assertEqual(model.skill_by_item["a"], "skill_1_2")
        self.assertNotIn("skill_1", model.skill_by_item.values())

    def test_seen_unknown_and_relevant_are_separate(self) -> None:
        train = [Interaction(1, "s", "seen", "skill_a", 1, "train", 0)]
        model = self.model(train)
        users = build_validation_users(train, [Interaction(2, "s", "seen", "skill_a", 0, "validation", 1), Interaction(3, "s", "new", "skill_a", 1, "validation", 2), Interaction(4, "s", "unknown", "skill_b", 1, "validation", 3)], model)
        user = users[0]
        self.assertEqual(user.relevant_items, frozenset())
        self.assertEqual(user.previously_seen_rows, 1)
        self.assertEqual(user.unknown_item_rows, 2)

    def test_popular_formula_and_stable_sort(self) -> None:
        rows = [Interaction(1, "s", "b", "skill_a", 1, "train", 0), Interaction(2, "t", "a", "skill_a", 1, "train", 1), Interaction(3, "t", "b", "skill_a", 1, "train", 2)]
        model = self.model(rows)
        self.assertEqual(model.popularity[model.item_index["b"]], 2)
        candidates = np.asarray([model.item_index["a"], model.item_index["b"]])
        self.assertEqual(top_items(model, candidates, np.asarray([1.0, 1.0]), np.asarray([2.0, 2.0])), ["a", "b"])

    def test_content_cosine_formula_and_fallback(self) -> None:
        train = [Interaction(1, "s", "a", "skill_a", 1, "train", 0), Interaction(2, "t", "b", "skill_b", 1, "train", 0), Interaction(3, "t", "c", "skill_a", 1, "train", 1)]
        model = self.model(train)
        user = build_validation_users(train, [Interaction(3, "s", "b", "skill_b", 1, "validation", 1)], model)[0]
        candidates, raw, fallback = candidate_scores(model, user)
        self.assertFalse(fallback["content"])
        self.assertAlmostEqual(raw["content"][list(candidates).index(model.item_index["b"])], 0.0)
        empty = UserCut("x", frozenset(), {}, frozenset(), 1, 0, 0, {})
        _, empty_raw, empty_fallback = candidate_scores(model, empty)
        self.assertTrue(empty_fallback["content"])
        np.testing.assert_array_equal(empty_raw["content"], empty_raw["popular"])

    def test_item_cf_binary_cosine_diagonal_and_fallback(self) -> None:
        rows = [Interaction(1, "s", "a", "skill_a", 0, "train", 0), Interaction(2, "s", "a", "skill_a", 1, "train", 1), Interaction(3, "s", "b", "skill_b", 1, "train", 2), Interaction(4, "t", "b", "skill_b", 1, "train", 0)]
        model = self.model(rows)
        self.assertEqual(model.item_similarity.diagonal().tolist(), [0.0, 0.0])
        self.assertAlmostEqual(model.item_similarity[model.item_index["a"], model.item_index["b"]], 1 / np.sqrt(2))
        empty = UserCut("x", frozenset(), {}, frozenset(), 1, 0, 0, {})
        _, raw, fallback = candidate_scores(model, empty)
        self.assertTrue(fallback["cf"])
        np.testing.assert_array_equal(raw["cf"], raw["popular"])

    def test_csr_index_order_does_not_change_cf_score(self) -> None:
        rows = [Interaction(1, "s", "a", "skill_a", 1, "train", 0), Interaction(2, "t", "b", "skill_b", 1, "train", 0), Interaction(3, "u", "c", "skill_a", 1, "train", 0)]
        model = self.model(rows)
        model.item_similarity.sort_indices()
        user = build_validation_users(rows, [Interaction(4, "s", "c", "skill_a", 1, "validation", 1)], model)[0]
        before = candidate_scores(model, user)[1]["cf"].copy()
        model.item_similarity = model.item_similarity.tocsc().tocsr()
        after = candidate_scores(model, user)[1]["cf"]
        np.testing.assert_allclose(before, after)

    def test_bkt_updates_after_answer(self) -> None:
        rows = [Interaction(1, "s", "a", "skill_a", 1, "train", 0)]
        model = self.model(rows)
        states = update_mastery(rows, model)
        params = BKTParams(0.5, 0.1, 0.1, 0.1)
        self.assertAlmostEqual(states["s"]["skill_a"], bkt_update(params.L0, 1, params))

    def test_weight_grids_are_15_and_20(self) -> None:
        self.assertEqual(len(weight_grid(3)), 15)
        self.assertEqual(len(weight_grid(4, require_kt=True)), 20)
        self.assertEqual(len(set(weight_grid(3))), 15)
        self.assertTrue(all(w[3] >= 0.25 for w in weight_grid(4, require_kt=True)))

    def test_weight_selection_rule(self) -> None:
        candidates = [{"weights": [0.5, 0.25, 0.25], "metrics": {"ndcgAt5": 0.5, "recallAt5": 0.1, "hitRateAt5": 0.1, "precisionAt5": 0.1}}, {"weights": [0.25, 0.5, 0.25], "metrics": {"ndcgAt5": 0.5, "recallAt5": 0.1, "hitRateAt5": 0.1, "precisionAt5": 0.1}}]
        self.assertEqual(select_weights(candidates)["weights"], [0.25, 0.5, 0.25])
        candidates[1]["metrics"]["precisionAt5"] = 0.2
        self.assertEqual(select_weights(candidates)["weights"], [0.25, 0.5, 0.25])

    def test_hybrid_components_are_per_user_minmax(self) -> None:
        np.testing.assert_allclose(minmax(np.asarray([2.0, 4.0, 6.0])), [0.0, 0.5, 1.0])
        np.testing.assert_allclose(minmax(np.asarray([4.0, 4.0])), [0.0, 0.0])

    def test_hybrid_fallback_components_are_counted(self) -> None:
        train = [Interaction(1, "s", "a", "skill_a", 1, "train", 0)]
        model = self.model(train)
        users = [UserCut("new", frozenset(), {}, frozenset(), 1, 0, 0, {})]
        reports, _ = evaluate_users(model, users)
        candidate = next(item for item in reports["HYBRID_NO_KT_CANDIDATES"] if item["weights"] == [0.5, 0.5, 0.0])
        self.assertEqual(candidate["metrics"]["fallbackComponents"], {"content": 1})
        self.assertEqual(candidate["metrics"]["fallbackUsers"], 1)

    def test_ablations_delete_only_and_renormalize(self) -> None:
        rows = [Interaction(1, "s", "a", "skill_a", 1, "train", 0), Interaction(2, "t", "b", "skill_b", 0, "train", 0)]
        model = self.model(rows)
        users = [UserCut("s", frozenset({"a"}), {"skill_a": 1}, frozenset({"b"}), 1, 0, 0, {})]
        report = evaluate_fixed_hybrid(model, users, (0.25, 0.25, 0.25, 0.25), "cf")
        self.assertEqual(report["deletedComponent"], "cf")
        self.assertEqual(report["weights"], [1 / 3, 1 / 3, 0.0, 1 / 3])

    def test_metric_denominators_and_diversity(self) -> None:
        model = self.model([Interaction(1, "s", "a", "skill_a", 1, "train", 0), Interaction(2, "s", "b", "skill_b", 1, "train", 1), Interaction(3, "t", "c", "skill_a", 1, "train", 0)])
        acc = MetricAccumulator()
        acc.add(["a"], frozenset(), model)
        report = acc.report(3, 0, 0, 1)
        self.assertEqual(report["precisionAt5"], 0.0)
        self.assertIsNone(report["recallAt5"])
        self.assertIsNone(report["diversity"])
        acc = MetricAccumulator()
        acc.add(["a", "b"], frozenset({"a"}), model)
        report = acc.report(3, 0, 0, 1)
        self.assertEqual(report["precisionAt5"], 0.2)
        self.assertEqual(report["diversity"], 1.0)
        self.assertEqual(report["uniqueRecommendedItems"], 2)

    def test_top5_unique_candidate_and_history_filter(self) -> None:
        train = [Interaction(1, "s", "a", "skill_a", 1, "train", 0), Interaction(2, "s", "b", "skill_b", 1, "train", 1)]
        model = self.model(train)
        user = build_validation_users(train, [Interaction(3, "s", "c", "skill_a", 1, "validation", 2)], model)[0]
        candidates, raw, _ = candidate_scores(model, user)
        top = top_items(model, candidates, raw["popular"], raw["popular"])
        self.assertEqual(len(top), len(set(top)))
        self.assertTrue(set(top).issubset({model.items[i] for i in candidates}))
        self.assertNotIn("a", top)
        self.assertNotIn("b", top)

    def test_formal_entry_marker_existing_head_and_hash_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "data/processed/g5-recommendation"
            out.mkdir(parents=True)
            (out / "TEST_EVALUATION_CONSUMED").write_text("x", encoding="utf-8")
            with patch("training.run_g5_recommendation_comparison.worktree_clean", return_value=True), patch("training.run_g5_recommendation_comparison.current_commit", return_value="head"), patch("training.run_g5_recommendation_comparison.assert_frozen_inputs", return_value=({}, {"tuningCommit": "protocol"}, {}, {}, {})):
                with self.assertRaises(RuntimeError):
                    evaluate_once("head", "protocol", root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("training.run_g5_recommendation_comparison.worktree_clean", return_value=True), patch("training.run_g5_recommendation_comparison.current_commit", return_value="other"):
                with self.assertRaises(RuntimeError):
                    evaluate_once("head", "protocol", root)
            self.assertFalse((root / "data/processed/g5-recommendation/TEST_EVALUATION_CONSUMED").exists())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("training.run_g5_recommendation_comparison.worktree_clean", return_value=True), patch("training.run_g5_recommendation_comparison.current_commit", return_value="head"), patch("training.run_g5_recommendation_comparison.assert_frozen_inputs", side_effect=RuntimeError("hash")):
                with self.assertRaises(RuntimeError):
                    evaluate_once("head", "protocol", root)
            self.assertFalse((root / "data/processed/g5-recommendation/TEST_EVALUATION_CONSUMED").exists())

    def test_formal_exception_keeps_marker_and_failed_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = ({"candidateCatalogSha256": "x", "candidateCount": 1}, {"tuningCommit": "protocol"}, {}, {}, {})
            with patch("training.run_g5_recommendation_comparison.worktree_clean", return_value=True), patch("training.run_g5_recommendation_comparison.current_commit", return_value="head"), patch("training.run_g5_recommendation_comparison.assert_frozen_inputs", return_value=frozen), patch("training.run_g5_recommendation_comparison.load_dataset", side_effect=ValueError("synthetic")):
                with self.assertRaises(ValueError):
                    evaluate_once("head", "protocol", root)
            self.assertTrue((root / "data/processed/g5-recommendation/TEST_EVALUATION_CONSUMED").exists())
            ledger = json.loads((root / "data/processed/g5-recommendation/evaluation-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["entries"][-1]["status"], "failed")

    def test_freeze_report_does_not_call_ranker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifacts").mkdir()
            protocol = {"passed": True, "protocolCommit": "h", "candidateCatalogSha256": "c"}
            tuning = {"passed": True, "validation": {}, "testSafety": {}}
            (root / "artifacts/g5-recommendation-protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
            (root / "artifacts/g5-recommendation-tuning.json").write_text(json.dumps(tuning), encoding="utf-8")
            with patch("training.run_g5_recommendation_comparison.worktree_clean", return_value=True), patch("training.run_g5_recommendation_comparison.current_commit", return_value="base"), patch("training.run_g5_recommendation_comparison.rank_user", side_effect=AssertionError("ranker called")):
                with self.assertRaises((RuntimeError, ValueError, FileNotFoundError)):
                    freeze_report("base", "protocol", root)

    def test_validate_frozen_detects_replaced_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "data/processed/g5-recommendation"
            (out / "rankings").mkdir(parents=True)
            (out / "TEST_EVALUATION_CONSUMED").write_text("x", encoding="utf-8")
            (out / "evaluation-ledger.json").write_text("{}", encoding="utf-8")
            path = out / "rankings/POPULAR.jsonl"
            path.write_text(json.dumps({"recommendations": ["a"]}) + "\n", encoding="utf-8")
            evaluation = {"testRankingCount": 8, "rankingFiles": {"POPULAR": {"path": "data/processed/g5-recommendation/rankings/POPULAR.jsonl", "sha256": "wrong", "rows": 1}}}
            (out / "test-evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_frozen(root)

    def test_synthetic_evaluate_freeze_validate_chain_uses_distinct_commits(self) -> None:
        model = type("Model", (), {"items": ["a"], "skill_by_item": {"a": "skill_a"}})()
        users = [UserCut("student", frozenset(), {}, frozenset({"a"}), 1, 0, 0, {})]
        protocol = {"passed": True, "protocolCommit": "protocol", "candidateCatalogSha256": "catalog", "candidateCount": 1, "rules": {}, "metricDefinitions": {}}
        tuning = {"passed": True, "tuningCommit": "protocol", "strategies": {}, "ablations": {}, "validation": {"hybridNoKt": {"selected": {"weights": [0.0, 0.0, 1.0]}}, "hybridKt": {"selected": {"weights": [0.0, 0.0, 0.75, 0.25]}}}}
        tuning_validation = {"passed": True, "selectedWeights": {"HYBRID_NO_KT": [0.0, 0.0, 1.0], "HYBRID_KT": [0.0, 0.0, 0.75, 0.25]}}
        frozen = (protocol, tuning, tuning_validation, {"input": "hash"}, {"files": []})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_paths = {"clean": root / "clean", "split": root / "split"}
            with patch("training.run_g5_recommendation_comparison.worktree_clean", return_value=True), patch("training.run_g5_recommendation_comparison.current_commit", return_value="base"), patch("training.run_g5_recommendation_comparison.assert_frozen_inputs", return_value=frozen), patch("training.run_g5_recommendation_comparison.input_paths", return_value=fake_paths), patch("training.run_g5_recommendation_comparison.load_dataset", return_value=({"train": [], "validation": [], "test": []}, ScanInfo())), patch("training.run_g5_recommendation_comparison.build_model", return_value=model), patch("training.run_g5_recommendation_comparison.build_test_users", return_value=users), patch("training.run_g5_recommendation_comparison.rank_user", return_value=(["a"], ())):
                evaluation = evaluate_once("base", "protocol", root)
                self.assertEqual(evaluation["protocolCommit"], "protocol")
                self.assertEqual(evaluation["evaluationBaseCommit"], "base")
                with patch("training.run_g5_recommendation_comparison.rank_user", side_effect=AssertionError("ranker called")):
                    comparison = freeze_report("base", "protocol", root)
                    validation = validate_frozen(root)
                self.assertTrue(comparison["passed"])
                self.assertTrue(validation["passed"])
                self.assertTrue((root / "artifacts/g5-recommendation-comparison.json").exists())
                self.assertTrue((root / "artifacts/g5-05-report-validation.json").exists())

    def test_validate_frozen_rejects_comparison_tuning_commit_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "data/processed/g5-recommendation"
            out.mkdir(parents=True)
            (root / "artifacts").mkdir()
            evaluation = {"testRankingCount": 8, "rankingFiles": {}}
            comparison = {"protocolCommit": "protocol", "tuningCommit": "wrong", "evaluationBaseCommit": "base"}
            (out / "test-evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")
            (out / "TEST_EVALUATION_CONSUMED").write_text(json.dumps({"status": "CONSUMED", "protocolCommit": "protocol", "tuningCommit": "protocol", "evaluationBaseCommit": "base"}), encoding="utf-8")
            (out / "evaluation-ledger.json").write_text(json.dumps({"entries": [{"status": "completed"}]}), encoding="utf-8")
            (root / "artifacts/g5-recommendation-comparison.json").write_text(json.dumps(comparison), encoding="utf-8")
            frozen = ({"passed": True, "protocolCommit": "protocol"}, {"passed": True, "tuningCommit": "protocol"}, {"passed": True, "selectedWeights": {}}, {}, {})
            with patch("training.run_g5_recommendation_comparison.assert_frozen_inputs", return_value=frozen):
                with self.assertRaisesRegex(ValueError, "commit binding"):
                    validate_frozen(root)


if __name__ == "__main__":
    unittest.main()

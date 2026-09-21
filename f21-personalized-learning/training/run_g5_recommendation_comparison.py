#!/usr/bin/env python3
"""Leakage-safe G5 recommendation protocol and evaluation entry points.

G5-05A runs only ``prepare``, ``tune`` and ``validate-tuning``.  The test
entry point is complete and one-shot protected for the later G5-05B gate, but
is intentionally never invoked by this batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import stat
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.metrics.pairwise import cosine_similarity

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.fit_g5_bkt import BKTParams, bkt_update  # noqa: E402

PROTOCOL_SCHEMA = "g5-recommendation-protocol-v2"
TUNING_SCHEMA = "g5-recommendation-tuning-v2"
TUNING_VALIDATION_SCHEMA = "g5-05-tuning-validation-v1"
FREEZE_REPORT_SCHEMA = "g5-05-freeze-report-v1"
FROZEN_VALIDATION_SCHEMA = "g5-05-report-validation-v1"
GENERATOR = "training/run_g5_recommendation_comparison.py"
TOP_K = 5
EPS = 1e-12
GRID_VALUES = (0.0, 0.25, 0.5, 0.75, 1.0)
ALLOWED_MODEL_PARTITIONS = frozenset({"train", "validation"})
CATALOG_VERSION = "NOT_APPLICABLE_ASSISTMENTS_PUBLIC_DATASET"
STRATEGY_VERSION = "G5_RECOMMENDATION_BASELINES_V2"


@dataclass(frozen=True)
class Interaction:
    source_row: int
    student: str
    item: str
    skill: str
    correct: int
    partition: str
    sequence_index: int


@dataclass
class UserCut:
    student: str
    history_items: frozenset[str]
    history_skill_counts: dict[str, int]
    relevant_items: frozenset[str]
    target_row_count: int
    unknown_item_rows: int
    previously_seen_rows: int
    mastery_by_skill: dict[str, float]


@dataclass
class ScanInfo:
    combinedCleanFileScanned: bool = True
    splitAssignmentsScanned: bool = True
    cleanLinesScanned: int = 0
    testCleanLinesParsedDuringStreaming: int = 0
    testInteractionObjectsRetained: bool = False
    testRelevantItemsRetained: bool = False
    testLabelsRetained: bool = False


@dataclass
class FrozenModel:
    items: list[str]
    item_index: dict[str, int]
    skill_by_item: dict[str, str]
    popularity: np.ndarray
    train_seen_by_student: dict[str, frozenset[str]]
    train_skill_counts_by_student: dict[str, dict[str, int]]
    mastery_by_student: dict[str, dict[str, float]]
    bkt_params_by_skill: dict[str, BKTParams]
    bkt_fallback: BKTParams
    item_similarity: csr_matrix


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def stable_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_commit(root: Path = ROOT) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def worktree_clean(root: Path = ROOT) -> bool:
    return not subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=root, check=False).returncode == 0


def committed_file_matches(root: Path, commit: str, relative_path: str) -> bool:
    try:
        prefix = subprocess.check_output(["git", "rev-parse", "--show-prefix"], cwd=root, text=True).strip()
        committed = subprocess.check_output(["git", "show", f"{commit}:{prefix}{relative_path}"], cwd=root)
    except subprocess.CalledProcessError:
        return False
    return committed == (root / relative_path).read_bytes()


def environment() -> dict[str, Any]:
    import scipy
    import sklearn

    executable = Path(sys.executable).absolute()
    venv_root = (ROOT / ".venv").resolve()
    if Path(sys.prefix).resolve() != venv_root or venv_root not in executable.parents:
        raise RuntimeError(f"formal artifact generation requires .venv Python, got prefix={sys.prefix} executable={executable}")
    return {
        "pythonVersion": sys.version,
        "pythonExecutable": str(executable),
        "pythonImplementation": platform.python_implementation(),
        "os": platform.system(),
        "osRelease": platform.release(),
        "machine": platform.machine(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "scikitLearn": sklearn.__version__,
        "device": "CPU",
    }


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def input_paths(root: Path = ROOT) -> dict[str, Path]:
    return {"clean": root / "data/processed/assistments-clean.jsonl", "split": root / "data/processed/g5-split-assignments.jsonl", "manifest": root / "data/manifests/g5-split-manifest.json", "bktParameters": root / "data/processed/g5-bkt-parameters.json", "bktFit": root / "artifacts/g5-bkt-fit.json"}


def input_hashes(paths: Mapping[str, Path]) -> dict[str, Any]:
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    if manifest.get("passed") is not True or manifest.get("invariants", {}).get("passed") is not True:
        raise ValueError("G5 split manifest gate failed")
    clean_hash = sha256_file(paths["clean"])
    split_hash = sha256_file(paths["split"])
    if clean_hash != manifest.get("cleanSha256") or split_hash != manifest.get("splitAssignmentSha256"):
        raise ValueError("clean/split input does not match frozen manifest")
    bkt_fit = json.loads(paths["bktFit"].read_text(encoding="utf-8"))
    if bkt_fit.get("passed") is not True or bkt_fit.get("selectedStrategyVersion") != "SKILL_SPECIFIC_FALLBACK":
        raise ValueError("frozen BKT gate failed")
    return {"cleanSha256": clean_hash, "splitSha256": split_hash, "manifestSha256": sha256_file(paths["manifest"]), "bktParametersSha256": sha256_file(paths["bktParameters"]), "bktFitSha256": sha256_file(paths["bktFit"]), "partitionHashes": manifest.get("partitionHashes", {})}


def protected_hashes(root: Path = ROOT) -> dict[str, Any]:
    comparison = json.loads((root / "artifacts/g5-kt-comparison.json").read_text(encoding="utf-8"))
    files = [{"path": item["path"], "sha256": item["sha256"]} for item in comparison["checkpoints"]]
    files += [{"path": item["path"], "sha256": item["sha256"]} for item in comparison["predictionFiles"]]
    files += [{"path": comparison["evaluationLedger"]["path"], "sha256": comparison["evaluationLedger"]["sha256"]}, {"path": comparison["correctiveConsumption"]["path"], "sha256": comparison["correctiveConsumption"]["sha256"]}]
    for item in files:
        if sha256_file(root / item["path"]) != item["sha256"]:
            raise ValueError(f"protected hash mismatch: {item['path']}")
    return {"source": "artifacts/g5-kt-comparison.json", "files": files}


def load_assignments(split_path: Path) -> dict[int, dict[str, Any]]:
    assignments: dict[int, dict[str, Any]] = {}
    with split_path.open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            assignments[int(item["sourceRow"])] = item
    return assignments


def load_dataset(clean_path: Path, split_path: Path, allowedPartitions: set[str] | frozenset[str]) -> tuple[dict[str, list[Interaction]], ScanInfo]:
    """Stream combined clean data while retaining Interaction only for allowed partitions."""
    if not allowedPartitions or not allowedPartitions.issubset({"train", "validation", "test"}):
        raise ValueError("allowedPartitions must be a subset of train/validation/test")
    assignments = load_assignments(split_path)
    scan = ScanInfo()
    grouped: dict[str, list[Interaction]] = {partition: [] for partition in allowedPartitions}
    with clean_path.open(encoding="utf-8") as handle:
        for line in handle:
            scan.cleanLinesScanned += 1
            row = json.loads(line)
            assignment = assignments.get(int(row["sourceRow"]))
            if assignment is None:
                raise ValueError(f"missing assignment for {row['sourceRow']}")
            partition = assignment["partition"]
            if partition == "test":
                scan.testCleanLinesParsedDuringStreaming += 1
            if partition not in allowedPartitions:
                continue
            if assignment["studentExternalId"] != row["studentExternalId"]:
                raise ValueError("student mismatch between clean row and split assignment")
            grouped[partition].append(Interaction(int(row["sourceRow"]), str(row["studentExternalId"]), str(row["itemExternalId"]), str(row["skillExternalId"]), int(row["correct"]), partition, int(assignment["sequenceIndex"])))
    scan.testInteractionObjectsRetained = "test" in allowedPartitions
    for values in grouped.values():
        values.sort(key=lambda event: (event.student, event.sequence_index, event.source_row))
    return grouped, scan


def load_allowed_interactions(clean_path: Path, split_path: Path, allowedPartitions: set[str] | frozenset[str]) -> dict[str, list[Interaction]]:
    return load_dataset(clean_path, split_path, allowedPartitions)[0]


def load_bkt_parameters(path: Path) -> tuple[BKTParams, dict[str, BKTParams]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    course = BKTParams(**payload["courseShared"])
    skills = {skill: BKTParams(**values) for skill, values in payload["skillSpecific"].items()}
    if payload.get("schemaVersion") != "g5-bkt-fit-v1" or not skills:
        raise ValueError("invalid BKT parameter file")
    return course, skills


def build_model(train_rows: Sequence[Interaction], paths: Mapping[str, Path]) -> FrozenModel:
    item_skills: dict[str, set[str]] = defaultdict(set)
    popularity: Counter[str] = Counter()
    seen: dict[str, set[str]] = defaultdict(set)
    skill_counts: dict[str, Counter[str]] = defaultdict(Counter)
    by_student: dict[str, list[Interaction]] = defaultdict(list)
    for row in train_rows:
        item_skills[row.item].add(row.skill)
        popularity[row.item] += 1
        seen[row.student].add(row.item)
        skill_counts[row.student][row.skill] += 1
        by_student[row.student].append(row)
    multi = {item: sorted(skills) for item, skills in item_skills.items() if len(skills) != 1}
    if multi:
        raise ValueError(f"train item has multiple skillExternalId values: {next(iter(multi.items()))}")
    items = sorted(item_skills)
    item_index = {item: i for i, item in enumerate(items)}
    skill_by_item = {item: next(iter(item_skills[item])) for item in items}
    popularity_array = np.asarray([popularity[item] for item in items], dtype=np.float64)
    students = sorted(by_student)
    rows: list[int] = []
    cols: list[int] = []
    for student_index, student in enumerate(students):
        for item in seen[student]:
            rows.append(student_index)
            cols.append(item_index[item])
    matrix = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(students), len(items)))
    matrix.sum_duplicates()
    matrix.data[:] = 1.0
    similarity = cosine_similarity(matrix.T, dense_output=False).tocsr()
    similarity.setdiag(0.0)
    similarity.eliminate_zeros()
    similarity.sort_indices()
    fallback, by_skill = load_bkt_parameters(paths["bktParameters"])
    mastery: dict[str, dict[str, float]] = defaultdict(dict)
    for student in students:
        for row in sorted(by_student[student], key=lambda event: (event.sequence_index, event.source_row)):
            params = by_skill.get(row.skill, fallback)
            old = mastery[student].get(row.skill, params.L0)
            mastery[student][row.skill] = bkt_update(old, row.correct, params)
    return FrozenModel(items, item_index, skill_by_item, popularity_array, {s: frozenset(v) for s, v in seen.items()}, {s: dict(v) for s, v in skill_counts.items()}, dict(mastery), by_skill, fallback, similarity)


def update_mastery(rows: Sequence[Interaction], model: FrozenModel) -> dict[str, dict[str, float]]:
    states: dict[str, dict[str, float]] = defaultdict(dict)
    for row in sorted(rows, key=lambda event: (event.student, event.sequence_index, event.source_row)):
        params = model.bkt_params_by_skill.get(row.skill, model.bkt_fallback)
        old = states[row.student].get(row.skill, params.L0)
        states[row.student][row.skill] = bkt_update(old, row.correct, params)
    return dict(states)


def build_cut_users(history_rows: Sequence[Interaction], target_rows: Sequence[Interaction], model: FrozenModel, mastery: Mapping[str, Mapping[str, float]]) -> list[UserCut]:
    histories: dict[str, list[Interaction]] = defaultdict(list)
    targets: dict[str, list[Interaction]] = defaultdict(list)
    for row in history_rows:
        histories[row.student].append(row)
    for row in target_rows:
        targets[row.student].append(row)
    users: list[UserCut] = []
    for student in sorted(targets):
        history = sorted(histories.get(student, []), key=lambda event: (event.sequence_index, event.source_row))
        future = sorted(targets[student], key=lambda event: (event.sequence_index, event.source_row))
        seen = frozenset(row.item for row in history)
        counts = Counter(row.skill for row in history)
        relevant: set[str] = set()
        unknown = previous = 0
        for row in future:
            if row.item not in model.item_index:
                unknown += 1
            elif row.item in seen:
                previous += 1
            else:
                relevant.add(row.item)
        users.append(UserCut(student, seen, dict(counts), frozenset(relevant), len(future), unknown, previous, dict(mastery.get(student, {}))))
    return users


def build_validation_users(train_rows: Sequence[Interaction], validation_rows: Sequence[Interaction], model: FrozenModel) -> list[UserCut]:
    return build_cut_users(train_rows, validation_rows, model, model.mastery_by_student)


def build_test_users(train_rows: Sequence[Interaction], validation_rows: Sequence[Interaction], test_rows: Sequence[Interaction], model: FrozenModel) -> list[UserCut]:
    history = list(train_rows) + list(validation_rows)
    return build_cut_users(history, test_rows, model, update_mastery(history, model))


def weight_grid(component_count: int, *, require_kt: bool = False) -> list[tuple[float, ...]]:
    if component_count not in (3, 4):
        raise ValueError("only three- and four-component grids are supported")
    return sorted(tuple(float(x) for x in candidate) for candidate in product(GRID_VALUES, repeat=component_count) if abs(sum(candidate) - 1.0) <= EPS and (not require_kt or candidate[3] >= 0.25))


def minmax(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values.copy()
    low, high = float(values.min()), float(values.max())
    return np.zeros_like(values, dtype=np.float64) if abs(high - low) <= EPS else (values - low) / (high - low)


def weighted_sum(weights: Sequence[float], values: np.ndarray) -> np.ndarray:
    """Weighted component sum without Accelerate's zero-weight matmul warning."""
    result = np.zeros(values.shape[1], dtype=np.float64)
    for weight, component in zip(weights, values):
        if weight:
            result += float(weight) * component
    return result


def candidate_scores(model: FrozenModel, user: UserCut) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, bool]]:
    seen_indices = np.asarray(sorted(model.item_index[item] for item in user.history_items if item in model.item_index), dtype=np.int32)
    candidates = np.setdiff1d(np.arange(len(model.items), dtype=np.int32), seen_indices, assume_unique=True)
    popular = model.popularity[candidates].copy()
    norm = math.sqrt(sum(value * value for value in user.history_skill_counts.values()))
    content = np.asarray([user.history_skill_counts.get(model.skill_by_item[model.items[index]], 0) / norm if norm else 0.0 for index in candidates], dtype=np.float64)
    content_fallback = norm <= EPS or (content.size > 0 and np.allclose(content, content[0], atol=EPS, rtol=0.0))
    if content_fallback:
        content = popular.copy()
    cf = np.zeros(candidates.size, dtype=np.float64)
    if seen_indices.size:
        history_row = csr_matrix((np.ones(seen_indices.size), ([0] * seen_indices.size, seen_indices)), shape=(1, len(model.items)))
        similarity_row = (history_row @ model.item_similarity).tocsr()
        similarity_row.sort_indices()
        if similarity_row.indices.size:
            positions = np.searchsorted(similarity_row.indices, candidates)
            valid = positions < similarity_row.indices.size
            valid &= similarity_row.indices[np.minimum(positions, similarity_row.indices.size - 1)] == candidates
            cf[valid] = similarity_row.data[positions[valid]]
    cf_fallback = seen_indices.size == 0 or cf.size == 0 or np.allclose(cf, cf[0], atol=EPS, rtol=0.0)
    if cf_fallback:
        cf = popular.copy()
    kt = np.asarray([1.0 - user.mastery_by_skill.get(model.skill_by_item[model.items[index]], model.bkt_params_by_skill.get(model.skill_by_item[model.items[index]], model.bkt_fallback).L0) for index in candidates], dtype=np.float64)
    return candidates, {"popular": popular, "content": content, "cf": cf, "kt": kt}, {"content": content_fallback, "cf": cf_fallback}


def top_items(model: FrozenModel, candidates: np.ndarray, final_scores: np.ndarray, popular: np.ndarray) -> list[str]:
    if candidates.size == 0:
        return []
    take = min(TOP_K, candidates.size)
    boundary = float(np.min(final_scores[np.argpartition(-final_scores, take - 1)[:take]]))
    better = np.flatnonzero(final_scores > boundary)
    ties = np.flatnonzero(final_scores == boundary)
    needed = take - better.size
    if needed < ties.size:
        tie_pop = popular[ties]
        pop_boundary = float(np.min(tie_pop[np.argpartition(-tie_pop, needed - 1)[:needed]]))
        pop_better = ties[tie_pop > pop_boundary]
        pop_ties = ties[tie_pop == pop_boundary]
        remaining = needed - pop_better.size
        if remaining < pop_ties.size:
            pop_ties = pop_ties[np.argsort(candidates[pop_ties], kind="stable")[:remaining]]
        ties = np.concatenate((pop_better, pop_ties))
    selected = np.concatenate((better, ties[:needed]))
    order = np.lexsort((candidates[selected], -popular[selected], -final_scores[selected]))
    return [model.items[int(index)] for index in candidates[selected[order[:take]]]]


class MetricAccumulator:
    def __init__(self) -> None:
        self.users = 0
        self.precision_sum = self.recall_sum = self.hitrate_sum = self.ndcg_sum = 0.0
        self.recall_users = self.no_relevant_users = self.short_lists = 0
        self.diversity_sum = 0.0
        self.diversity_users = 0
        self.recommended_items: set[str] = set()
        self.fallback_users = 0
        self.fallback_components: Counter[str] = Counter()

    def add(self, top: Sequence[str], relevant: frozenset[str], model: FrozenModel, fallback_components: Iterable[str] = ()) -> None:
        components = tuple(sorted(set(fallback_components)))
        self.users += 1
        self.fallback_users += int(bool(components))
        self.fallback_components.update(components)
        hits = len(set(top) & relevant)
        self.precision_sum += hits / TOP_K
        self.hitrate_sum += float(hits > 0)
        if relevant:
            self.recall_users += 1
            self.recall_sum += hits / len(relevant)
            dcg = sum(1.0 / math.log2(i + 2) for i, item in enumerate(top) if item in relevant)
            idcg = sum(1.0 / math.log2(i + 2) for i in range(min(TOP_K, len(relevant))))
            self.ndcg_sum += dcg / idcg if idcg else 0.0
        else:
            self.no_relevant_users += 1
        self.short_lists += int(len(top) < TOP_K)
        if len(top) >= 2:
            distances = [0.0 if model.skill_by_item[top[i]] == model.skill_by_item[top[j]] else 1.0 for i in range(len(top)) for j in range(i + 1, len(top))]
            self.diversity_sum += sum(distances) / len(distances)
            self.diversity_users += 1
        self.recommended_items.update(top)

    def report(self, candidate_count: int, unknown_rows: int, previous_rows: int, target_rows: int) -> dict[str, Any]:
        return {"validUsers": self.users, "noRelevantItemUsers": self.no_relevant_users, "precisionAt5": self.precision_sum / self.users if self.users else None, "recallAt5": self.recall_sum / self.recall_users if self.recall_users else None, "hitRateAt5": self.hitrate_sum / self.users if self.users else None, "ndcgAt5": self.ndcg_sum / self.recall_users if self.recall_users else None, "shortListCount": self.short_lists, "shortListRate": self.short_lists / self.users if self.users else None, "unknownItemRows": unknown_rows, "unknownItemRate": unknown_rows / target_rows if target_rows else None, "previouslySeenItemRows": previous_rows, "previouslySeenItemRate": previous_rows / target_rows if target_rows else None, "fallbackUsers": self.fallback_users, "fallbackRate": self.fallback_users / self.users if self.users else None, "fallbackComponents": dict(sorted(self.fallback_components.items())), "candidateDirectorySize": candidate_count, "uniqueRecommendedItems": len(self.recommended_items), "coverage": len(self.recommended_items) / candidate_count if candidate_count else None, "diversity": self.diversity_sum / self.diversity_users if self.diversity_users else None, "diversityComputableUsers": self.diversity_users, "metricTolerance": EPS}


def user_stats(users: Sequence[UserCut]) -> tuple[int, int, int, int]:
    return sum(user.unknown_item_rows for user in users), sum(user.previously_seen_rows for user in users), sum(user.target_row_count for user in users), len(users)


def evaluate_users(model: FrozenModel, users: Sequence[UserCut]) -> tuple[dict[str, Any], dict[str, Any]]:
    no_kt = weight_grid(3)
    kt = weight_grid(4, require_kt=True)
    base = {name: MetricAccumulator() for name in ("POPULAR", "CONTENT", "ITEM_CF")}
    no_kt_acc = {weights: MetricAccumulator() for weights in no_kt}
    kt_acc = {weights: MetricAccumulator() for weights in kt}
    unknown, previous, target_rows, _ = user_stats(users)
    for user in users:
        candidates, raw, fallback = candidate_scores(model, user)
        normalized = np.vstack([minmax(raw[name]) for name in ("popular", "content", "cf", "kt")]) if candidates.size else np.zeros((4, 0))
        base["POPULAR"].add(top_items(model, candidates, raw["popular"], raw["popular"]), user.relevant_items, model)
        base["CONTENT"].add(top_items(model, candidates, raw["content"], raw["popular"]), user.relevant_items, model, ("content",) if fallback["content"] else ())
        base["ITEM_CF"].add(top_items(model, candidates, raw["cf"], raw["popular"]), user.relevant_items, model, ("cf",) if fallback["cf"] else ())
        for weights, accumulator in no_kt_acc.items():
            score = weighted_sum(weights, normalized[:3])
            components = tuple(name for name, weight, key in zip(("popular", "content", "cf"), weights, (None, "content", "cf")) if weight > 0 and key and fallback[key])
            accumulator.add(top_items(model, candidates, score, raw["popular"]), user.relevant_items, model, components)
        for weights, accumulator in kt_acc.items():
            score = weighted_sum(weights, normalized)
            components = tuple(name for name, weight, key in zip(("popular", "content", "cf", "kt"), weights, (None, "content", "cf", None)) if weight > 0 and key and fallback[key])
            accumulator.add(top_items(model, candidates, score, raw["popular"]), user.relevant_items, model, components)
    common = (len(model.items), unknown, previous, target_rows)
    reports = {name: accumulator.report(*common) for name, accumulator in base.items()}
    reports["HYBRID_NO_KT_CANDIDATES"] = [{"weights": list(weights), "metrics": accumulator.report(*common)} for weights, accumulator in no_kt_acc.items()]
    reports["HYBRID_KT_CANDIDATES"] = [{"weights": list(weights), "metrics": accumulator.report(*common)} for weights, accumulator in kt_acc.items()]
    return reports, {"noKtWeights": len(no_kt), "ktWeights": len(kt)}


def select_weights(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def key(candidate: dict[str, Any]) -> tuple[Any, ...]:
        metrics = candidate["metrics"]
        return tuple(-float(metrics[name]) if metrics[name] is not None else float("inf") for name in ("ndcgAt5", "recallAt5", "hitRateAt5", "precisionAt5")) + (tuple(candidate["weights"]),)
    return min(candidates, key=key)


def evaluate_fixed_hybrid(model: FrozenModel, users: Sequence[UserCut], selected: tuple[float, ...], deleted: str) -> dict[str, Any]:
    names = ("popular", "content", "cf", "kt")
    values = dict(zip(names, selected))
    values[deleted] = 0.0
    total = sum(values.values())
    effective = (1.0, 0.0, 0.0, 0.0) if total <= EPS else tuple(values[name] / total for name in names)
    accumulator = MetricAccumulator()
    unknown, previous, target_rows, _ = user_stats(users)
    for user in users:
        candidates, raw, fallback = candidate_scores(model, user)
        if total <= EPS:
            accumulator.add(top_items(model, candidates, raw["popular"], raw["popular"]), user.relevant_items, model, ("popular",))
            continue
        normalized = np.vstack([minmax(raw[name]) for name in names]) if candidates.size else np.zeros((4, 0))
        components = tuple(name for name, weight in zip(names, effective) if weight > 0 and name in fallback and fallback[name])
        accumulator.add(top_items(model, candidates, weighted_sum(effective, normalized), raw["popular"]), user.relevant_items, model, components)
    report = accumulator.report(len(model.items), unknown, previous, target_rows)
    report.update({"deletedComponent": deleted, "weights": list(effective)})
    return report


def candidate_catalog(model: FrozenModel) -> dict[str, Any]:
    entries = [{"itemExternalId": item, "skillExternalId": model.skill_by_item[item], "trainInteractionCount": int(model.popularity[index])} for index, item in enumerate(model.items)]
    return {"items": entries, "sha256": sha256_bytes(stable_json(entries)), "size": len(entries)}


def leakage_fields(scan: ScanInfo, test_metrics: bool = False, test_ranking_count: int = 0) -> dict[str, Any]:
    return {"combinedCleanFileScanned": scan.combinedCleanFileScanned, "splitAssignmentsScanned": scan.splitAssignmentsScanned, "testCleanLinesParsedDuringStreaming": scan.testCleanLinesParsedDuringStreaming > 0, "testInteractionObjectsRetained": scan.testInteractionObjectsRetained, "testRelevantItemsRetained": scan.testRelevantItemsRetained, "testLabelsRetained": scan.testLabelsRetained, "testLabelsUsedForCandidateConstruction": False, "testLabelsUsedForFitting": False, "testLabelsUsedForWeightSelection": False, "testMetricsComputed": test_metrics, "testRankingCount": test_ranking_count, "testEvaluationStatus": "COMPLETED" if test_metrics else "DEFERRED_TO_G5_05B"}


def safety_flags(scan: ScanInfo, test_metrics: bool = False, test_ranking_count: int = 0) -> dict[str, Any]:
    fields = leakage_fields(scan, test_metrics, test_ranking_count)
    fields["testRowsMaterialized"] = scan.testInteractionObjectsRetained
    fields["testRelevantItemsMaterialized"] = scan.testRelevantItemsRetained
    return fields


def metric_definitions() -> dict[str, Any]:
    return {"hitCount": "|Top5 ∩ relevantItems|", "precisionAt5": "hitCount / 5 including short lists", "recallAt5": "hitCount / |relevantItems| excluding no-relevant users", "hitRateAt5": "1 iff hitCount > 0", "ndcgAt5": "binary relevance and IDCG length min(5, |relevantItems|)", "coverage": "unique recommended itemExternalId / train candidate count", "diversity": "mean pairwise 1-cosine of atomic skill one-hot vectors; null for list <2", "macro": "user-level macro average", "tolerance": EPS}


def protocol_rules() -> dict[str, Any]:
    return {"split": "按顺序切分; sequenceIndex/order_id is not real-time", "shortCoverage": "excluded from main evaluation", "topK": TOP_K, "relevance": "future itemExternalId deduplicated excluding unknownItem and previouslySeenItem", "candidateConstruction": "train-only item directory, skill map, popularity and Item-CF", "content": "atomic skillExternalId one-hot; composite token never split; correct unused", "itemCf": "binary train CSR, cosine column similarity, diagonal excluded", "hybrid": "per-user candidate-set min-max; constant component zero; positive-weight fallback counted", "ranking": "final score descending, raw POPULAR descending, itemExternalId ascending", "selection": "NDCG, Recall, HitRate, Precision descending then weight tuple lexicographic ascending", "seed": None, "randomnessUsed": False}


def commands() -> dict[str, str]:
    base = "--expected-head <evaluationBaseCommit> --expected-protocol-commit <protocolCommit>"
    return {"unittest": "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v", "prepare": "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python training/run_g5_recommendation_comparison.py prepare", "tune": "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python training/run_g5_recommendation_comparison.py tune", "validateTuning": "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python training/run_g5_recommendation_comparison.py validate-tuning", "evaluateOnce": f"PYTHONDONTWRITEBYTECODE=1 .venv/bin/python training/run_g5_recommendation_comparison.py evaluate-once {base}", "freezeReport": f"PYTHONDONTWRITEBYTECODE=1 .venv/bin/python training/run_g5_recommendation_comparison.py freeze-report {base}", "validateFrozen": "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python training/run_g5_recommendation_comparison.py validate-frozen"}


def common_artifact_fields(schema: str, started: float, hashes: dict[str, Any], catalog: dict[str, Any], protected: dict[str, Any], commit: str, scan: ScanInfo) -> dict[str, Any]:
    return {"passed": True, "schemaVersion": schema, "generator": {"script": GENERATOR, "schemaVersion": schema}, "gitCommit": commit, "protocolCommit": commit, "verifiedAt": utc_now(), "durationSeconds": round(time.monotonic() - started, 6), "environment": environment(), "input": hashes, "candidateCatalogSha256": catalog["sha256"], "candidateCount": catalog["size"], "catalogVersion": CATALOG_VERSION, "strategyVersion": STRATEGY_VERSION, "seed": None, "randomnessUsed": False, "rules": protocol_rules(), "metricDefinitions": metric_definitions(), "protectedG5Hashes": protected, "leakage": safety_flags(scan), "reproduceCommands": commands(), "limitations": ["ASSISTments public item identifiers have no separate catalog metadata.", "Formal test ranking is deferred to G5-05B."]}


def prepare(root: Path = ROOT) -> dict[str, Any]:
    started = time.monotonic()
    paths = input_paths(root)
    hashes = input_hashes(paths)
    protected = protected_hashes(root)
    grouped, scan = load_dataset(paths["clean"], paths["split"], ALLOWED_MODEL_PARTITIONS)
    model = build_model(grouped["train"], paths)
    catalog = candidate_catalog(model)
    out = root / "data/processed/g5-recommendation"
    save_json(out / "cache/candidate-catalog.json", catalog)
    commit = current_commit(root)
    payload = common_artifact_fields(PROTOCOL_SCHEMA, started, hashes, catalog, protected, commit, scan)
    payload.update({"allowedPartitions": sorted(ALLOWED_MODEL_PARTITIONS), "rowsMaterialized": {"train": len(grouped["train"]), "validation": len(grouped["validation"])}, "testSafety": safety_flags(scan)})
    save_json(root / "artifacts/g5-recommendation-protocol.json", payload)
    save_json(out / "prepare.json", payload)
    return payload


def tune(root: Path = ROOT) -> dict[str, Any]:
    started = time.monotonic()
    paths = input_paths(root)
    prepared = json.loads((root / "data/processed/g5-recommendation/prepare.json").read_text(encoding="utf-8"))
    hashes = input_hashes(paths)
    if prepared.get("input") != hashes:
        raise ValueError("prepare input hash mismatch")
    protected = protected_hashes(root)
    grouped, scan = load_dataset(paths["clean"], paths["split"], ALLOWED_MODEL_PARTITIONS)
    model = build_model(grouped["train"], paths)
    users = build_validation_users(grouped["train"], grouped["validation"], model)
    reports, grid_counts = evaluate_users(model, users)
    no_selected = select_weights(reports["HYBRID_NO_KT_CANDIDATES"])
    kt_selected = select_weights(reports["HYBRID_KT_CANDIDATES"])
    selected_kt = tuple(float(value) for value in kt_selected["weights"])
    ablations = {f"ABLATION_DROP_{component.upper()}": evaluate_fixed_hybrid(model, users, selected_kt, component) for component in ("kt", "cf", "content")}
    catalog = candidate_catalog(model)
    commit = prepared["protocolCommit"]
    payload = common_artifact_fields(TUNING_SCHEMA, started, hashes, catalog, protected, commit, scan)
    payload.update({"tuningCommit": commit, "validation": {"allowedPartitions": sorted(ALLOWED_MODEL_PARTITIONS), "validUsers": len(users), "gridCounts": grid_counts, "selectionRule": protocol_rules()["selection"], "hybridNoKt": {"selected": no_selected, "candidates": reports["HYBRID_NO_KT_CANDIDATES"]}, "hybridKt": {"selected": kt_selected, "candidates": reports["HYBRID_KT_CANDIDATES"]}}, "weightGrids": {"HYBRID_NO_KT": [list(item) for item in weight_grid(3)], "HYBRID_KT": [list(item) for item in weight_grid(4, require_kt=True)]}, "strategies": {"POPULAR": reports["POPULAR"], "CONTENT": reports["CONTENT"], "ITEM_CF": reports["ITEM_CF"], "HYBRID_NO_KT": {**no_selected["metrics"], "weights": no_selected["weights"]}, "HYBRID_KT": {**kt_selected["metrics"], "weights": kt_selected["weights"]}}, "ablations": ablations, "testSafety": safety_flags(scan)})
    save_json(root / "artifacts/g5-recommendation-tuning.json", payload)
    save_json(root / "data/processed/g5-recommendation/tuning.json", payload)
    return payload


def compare_json(actual: Any, expected: Any, path: str = "") -> list[str]:
    if isinstance(expected, float) and isinstance(actual, (float, int)):
        return [] if math.isclose(float(actual), expected, rel_tol=0.0, abs_tol=EPS) else [path]
    if type(actual) is not type(expected):
        return [path]
    if isinstance(expected, dict):
        errors: list[str] = []
        for key in expected:
            if key not in actual:
                errors.append(f"{path}.{key}")
            else:
                errors.extend(compare_json(actual[key], expected[key], f"{path}.{key}"))
        return errors
    if isinstance(expected, list):
        if len(actual) != len(expected):
            return [path]
        errors: list[str] = []
        for index, (left, right) in enumerate(zip(actual, expected)):
            errors.extend(compare_json(left, right, f"{path}[{index}]"))
        return errors
    return [] if actual == expected else [path]


def validate_tuning(root: Path = ROOT) -> dict[str, Any]:
    started = time.monotonic()
    protocol = json.loads((root / "artifacts/g5-recommendation-protocol.json").read_text(encoding="utf-8"))
    tuning = json.loads((root / "artifacts/g5-recommendation-tuning.json").read_text(encoding="utf-8"))
    paths = input_paths(root)
    hashes = input_hashes(paths)
    protected = protected_hashes(root)
    grouped, scan = load_dataset(paths["clean"], paths["split"], ALLOWED_MODEL_PARTITIONS)
    model = build_model(grouped["train"], paths)
    users = build_validation_users(grouped["train"], grouped["validation"], model)
    reports, _ = evaluate_users(model, users)
    no_selected = select_weights(reports["HYBRID_NO_KT_CANDIDATES"])
    kt_selected = select_weights(reports["HYBRID_KT_CANDIDATES"])
    selected_kt = tuple(float(value) for value in kt_selected["weights"])
    ablations = {f"ABLATION_DROP_{component.upper()}": evaluate_fixed_hybrid(model, users, selected_kt, component) for component in ("kt", "cf", "content")}
    errors: list[str] = []
    for name in ("POPULAR", "CONTENT", "ITEM_CF"):
        errors.extend(compare_json(reports[name], tuning["strategies"][name], name))
    errors.extend(compare_json(ablations, tuning["ablations"], "ablations"))
    actual_hybrid_no = {**no_selected["metrics"], "weights": no_selected["weights"]}
    actual_hybrid_kt = {**kt_selected["metrics"], "weights": kt_selected["weights"]}
    errors.extend(compare_json(actual_hybrid_no, tuning["strategies"]["HYBRID_NO_KT"], "HYBRID_NO_KT"))
    errors.extend(compare_json(actual_hybrid_kt, tuning["strategies"]["HYBRID_KT"], "HYBRID_KT"))
    if no_selected["weights"] != tuning["validation"]["hybridNoKt"]["selected"]["weights"] or kt_selected["weights"] != tuning["validation"]["hybridKt"]["selected"]["weights"]:
        errors.append("selectedWeights")
    if len(reports["HYBRID_NO_KT_CANDIDATES"]) != 15 or len(reports["HYBRID_KT_CANDIDATES"]) != 20:
        errors.append("weightGrid")
    catalog = candidate_catalog(model)
    if catalog["sha256"] != protocol["candidateCatalogSha256"] or catalog["sha256"] != tuning["candidateCatalogSha256"]:
        errors.append("candidateCatalogSha256")
    if errors:
        raise ValueError("independent tuning validation failed: " + ", ".join(errors[:10]))
    payload = {"passed": True, "schemaVersion": TUNING_VALIDATION_SCHEMA, "generator": {"script": GENERATOR, "schemaVersion": TUNING_VALIDATION_SCHEMA, "mode": "independent-recompute"}, "gitCommit": current_commit(root), "protocolCommit": protocol["protocolCommit"], "tuningCommit": tuning["tuningCommit"], "verifiedAt": utc_now(), "durationSeconds": round(time.monotonic() - started, 6), "environment": environment(), "input": hashes, "candidateCatalogSha256": catalog["sha256"], "candidateCount": catalog["size"], "catalogVersion": CATALOG_VERSION, "strategyVersion": STRATEGY_VERSION, "seed": None, "randomnessUsed": False, "weightGrids": {"HYBRID_NO_KT": [list(item) for item in weight_grid(3)], "HYBRID_KT": [list(item) for item in weight_grid(4, require_kt=True)]}, "selectedWeights": {"HYBRID_NO_KT": no_selected["weights"], "HYBRID_KT": kt_selected["weights"]}, "strategies": {"POPULAR": reports["POPULAR"], "CONTENT": reports["CONTENT"], "ITEM_CF": reports["ITEM_CF"], "HYBRID_NO_KT": actual_hybrid_no, "HYBRID_KT": actual_hybrid_kt}, "ablations": ablations, "leakage": safety_flags(scan), "protectedG5Hashes": protected, "reproduceCommands": commands(), "limitations": ["Validation-only independent recomputation; test Interaction objects are not retained."]}
    save_json(root / "artifacts/g5-05-tuning-validation.json", payload)
    return payload


def ranking_path(root: Path, strategy: str) -> Path:
    return root / "data/processed/g5-recommendation/rankings" / f"{strategy}.jsonl"


def rank_user(model: FrozenModel, user: UserCut, strategy: str, weights: tuple[float, ...] | None = None) -> tuple[list[str], tuple[str, ...]]:
    candidates, raw, fallback = candidate_scores(model, user)
    if strategy == "POPULAR":
        return top_items(model, candidates, raw["popular"], raw["popular"]), ()
    if strategy == "CONTENT":
        return top_items(model, candidates, raw["content"], raw["popular"]), (("content",) if fallback["content"] else ())
    if strategy == "ITEM_CF":
        return top_items(model, candidates, raw["cf"], raw["popular"]), (("cf",) if fallback["cf"] else ())
    if weights is None:
        raise ValueError("hybrid strategy requires weights")
    names = ("popular", "content", "cf", "kt")
    normalized = np.vstack([minmax(raw[name]) for name in names]) if candidates.size else np.zeros((4, 0))
    components = tuple(name for name, weight in zip(names, weights) if weight > 0 and fallback.get(name, False))
    return top_items(model, candidates, weighted_sum(weights, normalized), raw["popular"]), components


def write_ledger(path: Path, entry: dict[str, Any]) -> None:
    payload = {"schemaVersion": "g5-recommendation-evaluation-ledger-v1", "entries": []}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("entries", []).append(entry)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def update_last_ledger(path: Path, updates: dict[str, Any]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"][-1].update(updates)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def assert_frozen_inputs(root: Path, expected_protocol_commit: str, evaluation_base_commit: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol = json.loads((root / "artifacts/g5-recommendation-protocol.json").read_text(encoding="utf-8"))
    tuning = json.loads((root / "artifacts/g5-recommendation-tuning.json").read_text(encoding="utf-8"))
    tuning_validation = json.loads((root / "artifacts/g5-05-tuning-validation.json").read_text(encoding="utf-8"))
    if not all(item.get("passed") is True for item in (protocol, tuning, tuning_validation)):
        raise RuntimeError("protocol/tuning/tuning-validation artifact must be passed")
    bindings = (
        protocol.get("protocolCommit"), protocol.get("gitCommit"),
        tuning.get("protocolCommit"), tuning.get("tuningCommit"), tuning.get("gitCommit"),
        tuning_validation.get("protocolCommit"), tuning_validation.get("tuningCommit"), tuning_validation.get("gitCommit"),
    )
    if any(binding != expected_protocol_commit for binding in bindings):
        raise RuntimeError("protocol/tuning artifact commit binding mismatch")
    if not is_ancestor(root, expected_protocol_commit, evaluation_base_commit):
        raise RuntimeError("protocolCommit is not an ancestor of evaluationBaseCommit")
    for relative_path in ("artifacts/g5-recommendation-protocol.json", "artifacts/g5-recommendation-tuning.json", "artifacts/g5-05-tuning-validation.json"):
        if not committed_file_matches(root, evaluation_base_commit, relative_path):
            raise RuntimeError(f"frozen artifact differs from evaluationBaseCommit: {relative_path}")
    hashes = input_hashes(input_paths(root))
    protected = protected_hashes(root)
    if protocol.get("input") != hashes or tuning.get("input") != hashes or tuning_validation.get("input") != hashes:
        raise RuntimeError("input hash mismatch")
    if (protocol.get("candidateCatalogSha256") != tuning.get("candidateCatalogSha256") or protocol.get("candidateCatalogSha256") != tuning_validation.get("candidateCatalogSha256") or protocol.get("candidateCount") != tuning.get("candidateCount") or protocol.get("candidateCount") != tuning_validation.get("candidateCount")):
        raise RuntimeError("candidate directory mismatch")
    if any(item.get("protectedG5Hashes") != protected for item in (protocol, tuning, tuning_validation)):
        raise RuntimeError("protected G5 hash mismatch")
    return protocol, tuning, tuning_validation, hashes, protected


def evaluate_once(expected_head: str, expected_protocol_commit: str, root: Path = ROOT) -> dict[str, Any]:
    """Complete one-shot test evaluator for G5-05B; not invoked by R1."""
    if not worktree_clean(root):
        raise RuntimeError("evaluate-once requires clean worktree")
    if current_commit(root) != expected_head:
        raise RuntimeError("current HEAD does not equal --expected-head")
    protocol, tuning, _, hashes, protected = assert_frozen_inputs(root, expected_protocol_commit, expected_head)
    out = root / "data/processed/g5-recommendation"
    marker = out / "TEST_EVALUATION_CONSUMED"
    ledger = out / "evaluation-ledger.json"
    if marker.exists() or ledger.exists() or (out / "rankings").exists():
        raise RuntimeError("formal test evidence already exists")
    out.mkdir(parents=True, exist_ok=True)
    fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"status": "CONSUMED_PENDING", "protocolCommit": expected_protocol_commit, "tuningCommit": tuning["tuningCommit"], "evaluationBaseCommit": expected_head, "createdAt": utc_now()}, sort_keys=True) + "\n")
    started_at = utc_now()
    started = time.monotonic()
    try:
        write_ledger(ledger, {"status": "running", "startedAt": started_at, "protocolCommit": expected_protocol_commit, "tuningCommit": tuning["tuningCommit"], "evaluationBaseCommit": expected_head, "input": hashes})
        paths = input_paths(root)
        grouped, scan = load_dataset(paths["clean"], paths["split"], {"train", "validation", "test"})
        model = build_model(grouped["train"], paths)
        users = build_test_users(grouped["train"], grouped["validation"], grouped["test"], model)
        scan.testRelevantItemsRetained = True
        scan.testLabelsRetained = True
        no_kt = tuple(tuning["validation"]["hybridNoKt"]["selected"]["weights"]) + (0.0,)
        kt = tuple(tuning["validation"]["hybridKt"]["selected"]["weights"])
        strategies: dict[str, tuple[str, tuple[float, ...] | None]] = {"POPULAR": ("POPULAR", None), "CONTENT": ("CONTENT", None), "ITEM_CF": ("ITEM_CF", None), "HYBRID_NO_KT": ("HYBRID_NO_KT", no_kt), "HYBRID_KT": ("HYBRID_KT", kt)}
        for name, component in (("ABLATION_DROP_KT", "kt"), ("ABLATION_DROP_CF", "cf"), ("ABLATION_DROP_CONTENT", "content")):
            values = dict(zip(("popular", "content", "cf", "kt"), kt))
            values[component] = 0.0
            total = sum(values.values())
            strategies[name] = ("HYBRID_KT", tuple(values[key] / total for key in values) if total else (1.0, 0.0, 0.0, 0.0))
        ranking_files: dict[str, dict[str, Any]] = {}
        unknown, previous, target_rows, _ = user_stats(users)
        accumulators = {name: MetricAccumulator() for name in strategies}
        (out / "rankings").mkdir(parents=True, exist_ok=False)
        for name, (strategy, weights) in strategies.items():
            path = ranking_path(root, name)
            with path.open("w", encoding="utf-8") as handle:
                for user in users:
                    top, fallback = rank_user(model, user, strategy, weights)
                    accumulators[name].add(top, user.relevant_items, model, fallback)
                    handle.write(json.dumps({"studentExternalId": user.student, "recommendations": top, "fallbackComponents": list(fallback)}, sort_keys=True) + "\n")
            ranking_files[name] = {"path": str(path.relative_to(root)), "sha256": sha256_file(path), "rows": len(users)}
        effective_ablations = {name: list(weights) for name, (strategy, weights) in strategies.items() if name.startswith("ABLATION_") and strategy == "HYBRID_KT" and weights is not None}
        completed_at = utc_now()
        result = {"passed": True, "schemaVersion": "g5-recommendation-test-evaluation-v2", "protocolCommit": expected_protocol_commit, "tuningCommit": tuning["tuningCommit"], "evaluationBaseCommit": expected_head, "startedAt": started_at, "completedAt": completed_at, "durationSeconds": round(time.monotonic() - started, 6), "evaluationEnvironment": environment(), "input": hashes, "candidateCatalogSha256": protocol["candidateCatalogSha256"], "candidateCount": protocol["candidateCount"], "catalogVersion": CATALOG_VERSION, "strategyVersion": STRATEGY_VERSION, "seed": None, "randomnessUsed": False, "selectedWeights": {"HYBRID_NO_KT": list(no_kt), "HYBRID_KT": list(kt)}, "ablationWeights": effective_ablations, "testRankingCount": len(strategies), "rankingFiles": ranking_files, "userCount": len(users), "userIds": [user.student for user in users], "testTargetRowCount": target_rows, "unknownItemRows": unknown, "previouslySeenItemRows": previous, "metrics": {name: accumulator.report(len(model.items), unknown, previous, target_rows) for name, accumulator in accumulators.items()}, "testSafety": safety_flags(scan, True, len(strategies)), "protectedG5Hashes": protected, "limitations": ["ASSISTments public logs evaluate item recommendation only.", "A single test-boundary ranking is matched against later ordered interactions; this does not establish learning gains."]}
        evaluation_path = out / "test-evaluation.json"
        save_json(evaluation_path, result)
        update_last_ledger(ledger, {"status": "completed", "completedAt": completed_at, "rankingFiles": ranking_files, "testEvaluationSha256": sha256_file(evaluation_path), "testRankingCount": len(strategies)})
        marker.write_text(json.dumps({"status": "CONSUMED", "protocolCommit": expected_protocol_commit, "tuningCommit": tuning["tuningCommit"], "evaluationBaseCommit": expected_head, "consumedAt": utc_now(), "testEvaluationSha256": sha256_file(evaluation_path), "ledgerSha256": sha256_file(ledger), "rankingSha256": {name: item["sha256"] for name, item in ranking_files.items()}}, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(marker, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return result
    except Exception as exc:
        failure = {"status": "failed", "failedAt": utc_now(), "failureStage": "test-evaluation", "exceptionType": type(exc).__name__, "failureMessage": str(exc)}
        try:
            if ledger.exists():
                update_last_ledger(ledger, failure)
            else:
                write_ledger(ledger, {"protocolCommit": expected_protocol_commit, "tuningCommit": tuning["tuningCommit"], "evaluationBaseCommit": expected_head, **failure})
        except Exception:
            pass
        raise


def freeze_report(expected_head: str, expected_protocol_commit: str, root: Path = ROOT) -> dict[str, Any]:
    """Freeze persisted evidence; this function never ranks or evaluates a user."""
    if not worktree_clean(root) or current_commit(root) != expected_head:
        raise RuntimeError("freeze-report requires the clean evaluationBaseCommit")
    protocol, tuning, tuning_validation, hashes, protected = assert_frozen_inputs(root, expected_protocol_commit, expected_head)
    out = root / "data/processed/g5-recommendation"
    evaluation_path, marker_path, ledger_path = out / "test-evaluation.json", out / "TEST_EVALUATION_CONSUMED", out / "evaluation-ledger.json"
    if not all(path.exists() for path in (evaluation_path, marker_path, ledger_path)):
        raise ValueError("formal test evidence is incomplete")
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if evaluation.get("passed") is not True or marker.get("status") != "CONSUMED" or not ledger.get("entries") or ledger["entries"][-1].get("status") != "completed":
        raise ValueError("formal test evidence is not completed")
    if any(evaluation.get(key) != value for key, value in (("protocolCommit", expected_protocol_commit), ("tuningCommit", tuning["tuningCommit"]), ("evaluationBaseCommit", expected_head))):
        raise ValueError("test evaluation commit binding mismatch")
    ranking_files = evaluation.get("rankingFiles", {})
    if len(ranking_files) != 8 or evaluation.get("testRankingCount") != 8:
        raise ValueError("expected eight persisted rankings")
    for item in ranking_files.values():
        path = root / item["path"]
        if not path.exists() or sha256_file(path) != item["sha256"]:
            raise ValueError("ranking hash mismatch")
    if marker.get("testEvaluationSha256") != sha256_file(evaluation_path) or marker.get("ledgerSha256") != sha256_file(ledger_path):
        raise ValueError("marker hash mismatch")
    if marker.get("rankingSha256") != {name: item["sha256"] for name, item in ranking_files.items()} or evaluation.get("protectedG5Hashes") != protected:
        raise ValueError("marker ranking or protected hash mismatch")
    report = {"passed": True, "schemaVersion": "g5-recommendation-comparison-v1", "generator": {"script": GENERATOR, "schemaVersion": "g5-recommendation-comparison-v1"}, "protocolCommit": expected_protocol_commit, "tuningCommit": tuning["tuningCommit"], "evaluationBaseCommit": expected_head, "reportGenerationCommit": current_commit(root), "verifiedAt": utc_now(), "input": hashes, "candidateCatalogSha256": protocol["candidateCatalogSha256"], "candidateCount": protocol["candidateCount"], "catalogVersion": CATALOG_VERSION, "strategyVersion": STRATEGY_VERSION, "rules": protocol["rules"], "metricDefinitions": protocol["metricDefinitions"], "validation": {"selectedWeights": tuning_validation["selectedWeights"], "strategies": tuning["strategies"], "ablations": tuning["ablations"]}, "testEvaluation": evaluation, "rankingFiles": ranking_files, "evaluationLedger": {"path": str(ledger_path.relative_to(root)), "sha256": sha256_file(ledger_path)}, "testConsumption": {"path": str(marker_path.relative_to(root)), "sha256": sha256_file(marker_path)}, "testEvaluationFile": {"path": str(evaluation_path.relative_to(root)), "sha256": sha256_file(evaluation_path)}, "evaluationEnvironment": evaluation["evaluationEnvironment"], "reportGenerationEnvironment": environment(), "protectedG5Hashes": protected, "limits": {"testEvaluationCount": 1, "testRankingCount": 8, "testNotUsedForTuning": True}, "limitations": evaluation["limitations"], "reproduceCommands": commands()}
    save_json(root / "artifacts/g5-recommendation-comparison.json", report)
    return report


def validate_frozen(root: Path = ROOT) -> dict[str, Any]:
    """Independently validate persisted test rankings without invoking rank_user."""
    out = root / "data/processed/g5-recommendation"
    evaluation_path = out / "test-evaluation.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    if not (out / "TEST_EVALUATION_CONSUMED").exists() or not (out / "evaluation-ledger.json").exists() or evaluation.get("testRankingCount") != 8:
        raise ValueError("frozen test evidence incomplete")
    for strategy, item in evaluation.get("rankingFiles", {}).items():
        path = root / item["path"]
        if not path.exists() or sha256_file(path) != item["sha256"]:
            raise ValueError(f"ranking hash mismatch: {strategy}")
    comparison_path = root / "artifacts/g5-recommendation-comparison.json"
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    protocol_commit = comparison.get("protocolCommit")
    evaluation_base = comparison.get("evaluationBaseCommit")
    protocol, tuning, tuning_validation, hashes, protected = assert_frozen_inputs(root, protocol_commit, evaluation_base)
    marker = json.loads((out / "TEST_EVALUATION_CONSUMED").read_text(encoding="utf-8"))
    if marker.get("status") != "CONSUMED" or marker.get("protocolCommit") != protocol.get("protocolCommit") or marker.get("tuningCommit") != tuning.get("tuningCommit") or marker.get("evaluationBaseCommit") != evaluation_base:
        raise ValueError("test consumption marker is not finalized")
    ledger = json.loads((out / "evaluation-ledger.json").read_text(encoding="utf-8"))
    if not ledger.get("entries") or ledger["entries"][-1].get("status") != "completed":
        raise ValueError("evaluation ledger is not completed")
    if (
        protocol.get("passed") is not True
        or tuning.get("passed") is not True
        or tuning_validation.get("passed") is not True
        or comparison.get("tuningCommit") != tuning.get("tuningCommit")
        or evaluation.get("protocolCommit") != protocol_commit
        or evaluation.get("tuningCommit") != tuning.get("tuningCommit")
        or evaluation.get("evaluationBaseCommit") != evaluation_base
        or evaluation.get("protectedG5Hashes") != protected
    ):
        raise ValueError("frozen artifact commit binding mismatch")
    paths = input_paths(root)
    grouped, scan = load_dataset(paths["clean"], paths["split"], {"train", "validation", "test"})
    model = build_model(grouped["train"], paths)
    users = build_test_users(grouped["train"], grouped["validation"], grouped["test"], model)
    scan.testRelevantItemsRetained = True
    scan.testLabelsRetained = True
    if evaluation.get("userCount") != len(users) or evaluation.get("userIds") != [user.student for user in users]:
        raise ValueError("persisted ranking user sequence mismatch")
    expected_strategies = {"POPULAR", "CONTENT", "ITEM_CF", "HYBRID_NO_KT", "HYBRID_KT", "ABLATION_DROP_KT", "ABLATION_DROP_CF", "ABLATION_DROP_CONTENT"}
    if set(evaluation.get("rankingFiles", {})) != expected_strategies:
        raise ValueError("frozen strategy set mismatch")
    unknown, previous, target_rows, _ = user_stats(users)
    recomputed_metrics: dict[str, Any] = {}
    for strategy, item in evaluation["rankingFiles"].items():
        path = root / item["path"]
        if not path.exists() or sha256_file(path) != item["sha256"]:
            raise ValueError(f"ranking hash mismatch: {strategy}")
        accumulator = MetricAccumulator()
        with path.open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if len(rows) != len(users):
            raise ValueError(f"persisted ranking row count mismatch: {strategy}")
        for user, row in zip(users, rows):
            if row.get("studentExternalId") != user.student:
                raise ValueError(f"persisted ranking user mismatch: {strategy}")
            recommendations = row.get("recommendations")
            if not isinstance(recommendations, list) or len(recommendations) != len(set(recommendations)) or len(recommendations) > TOP_K:
                raise ValueError(f"invalid persisted ranking: {strategy}")
            candidate_items = set(model.items) - set(user.history_items)
            if not set(recommendations).issubset(candidate_items):
                raise ValueError(f"persisted ranking contains non-candidate item: {strategy}")
            accumulator.add(recommendations, user.relevant_items, model, row.get("fallbackComponents", ()))
        recomputed_metrics[strategy] = accumulator.report(len(model.items), unknown, previous, target_rows)
    if (
        compare_json(recomputed_metrics, evaluation.get("metrics", {}), "metrics")
        or compare_json(recomputed_metrics, comparison.get("testEvaluation", {}).get("metrics", {}), "comparison.metrics")
        or compare_json(tuning_validation.get("selectedWeights"), comparison.get("validation", {}).get("selectedWeights"), "comparison.validation.selectedWeights")
    ):
        raise ValueError("persisted metrics do not match rankings")
    expected_safety = safety_flags(scan, True, len(expected_strategies))
    if compare_json(expected_safety, evaluation.get("testSafety", {}), "testSafety"):
        raise ValueError("test safety metadata mismatch")
    if comparison.get("evaluationLedger", {}).get("sha256") != sha256_file(out / "evaluation-ledger.json") or comparison.get("testConsumption", {}).get("sha256") != sha256_file(out / "TEST_EVALUATION_CONSUMED") or comparison.get("testEvaluationFile", {}).get("sha256") != sha256_file(evaluation_path):
        raise ValueError("comparison evidence hash mismatch")
    result = {"passed": True, "schemaVersion": FROZEN_VALIDATION_SCHEMA, "generator": {"script": GENERATOR, "schemaVersion": FROZEN_VALIDATION_SCHEMA, "mode": "independent-ranking-recompute"}, "protocolCommit": protocol_commit, "tuningCommit": tuning["tuningCommit"], "evaluationBaseCommit": evaluation_base, "verifiedAt": utc_now(), "input": hashes, "candidateCatalogSha256": protocol["candidateCatalogSha256"], "candidateCount": protocol["candidateCount"], "rankingSha256": {name: item["sha256"] for name, item in evaluation["rankingFiles"].items()}, "testRankingCount": evaluation["testRankingCount"], "metrics": recomputed_metrics, "testSafety": expected_safety, "protectedG5Hashes": protected, "gates": {"marker": True, "ledger": True, "rankingHashes": True, "userSequence": True, "candidateEligibility": True, "metricsRecomputed": True, "comparisonHashes": True, "protectedG5Hashes": True}}
    save_json(root / "artifacts/g5-05-report-validation.json", result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "tune", "validate-tuning", "evaluate-once", "freeze-report", "validate-frozen"))
    parser.add_argument("--expected-head")
    parser.add_argument("--expected-protocol-commit")
    args = parser.parse_args(argv)
    if args.mode == "prepare":
        result = prepare()
    elif args.mode == "tune":
        result = tune()
    elif args.mode == "validate-tuning":
        result = validate_tuning()
    elif args.mode in {"evaluate-once", "freeze-report"}:
        if not args.expected_head or not args.expected_protocol_commit:
            parser.error(f"{args.mode} requires --expected-head and --expected-protocol-commit")
        result = evaluate_once(args.expected_head, args.expected_protocol_commit) if args.mode == "evaluate-once" else freeze_report(args.expected_head, args.expected_protocol_commit)
    else:
        result = validate_frozen()
    print(json.dumps({"passed": result["passed"], "schemaVersion": result["schemaVersion"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

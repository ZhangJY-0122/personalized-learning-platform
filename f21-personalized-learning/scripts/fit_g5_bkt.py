#!/usr/bin/env python3
"""Fit frozen train-only G5 BKT strategies; test evaluation is deferred to G5-04."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.clean_split_assistments import git_tracked, sha256_file, utc_now
except ModuleNotFoundError:
    from clean_split_assistments import git_tracked, sha256_file, utc_now

SCHEMA_VERSION = "g5-bkt-fit-v1"
FORMULA_VERSION = "bkt-four-parameter-v1"
MIN_SKILL_EVENTS = 100
SEED = 11
MAX_CANDIDATES = 100
DEFAULT_MAX_ATTEMPTS = MAX_CANDIDATES * 20
EPS = 1e-6
VECTOR_HASH = "7e88ffdc32987d35c6b53d607236b58c6fe2043a33e117bd49e4c2000d2d909b"


@dataclass(frozen=True)
class BKTParams:
    L0: float
    T: float
    G: float
    S: float


def valid_params(params: BKTParams) -> bool:
    return (0.05 <= params.L0 <= 0.95 and 0.001 <= params.T <= 0.30 and 0.01 <= params.G <= 0.40 and 0.01 <= params.S <= 0.40 and params.G + params.S < 1)


def bkt_predict(p_l: float, params: BKTParams) -> float:
    return p_l * (1.0 - params.S) + (1.0 - p_l) * params.G


def bkt_update(p_l: float, correct: int, params: BKTParams) -> float:
    prediction = bkt_predict(p_l, params)
    if correct == 1:
        posterior = p_l * (1.0 - params.S) / prediction if prediction > 0 else p_l
    else:
        denominator = p_l * params.S + (1.0 - p_l) * (1.0 - params.G)
        posterior = p_l * params.S / denominator if denominator > 0 else p_l
    return posterior + (1.0 - posterior) * params.T


def clipped(probability: float) -> float:
    return min(1.0 - EPS, max(EPS, probability))


def logloss(labels: Iterable[int], probabilities: Iterable[float]) -> float:
    values = [-int(label) * math.log(clipped(prob)) - (1 - int(label)) * math.log(1.0 - clipped(prob)) for label, prob in zip(labels, probabilities)]
    return sum(values) / len(values) if values else float("nan")


def generate_candidates(seed: int = SEED, maximum: int = MAX_CANDIDATES, max_attempts: int | None = None, *, return_stats: bool = False) -> list[BKTParams] | tuple[list[BKTParams], dict[str, Any]]:
    import random
    rng = random.Random(seed)
    candidates: list[BKTParams] = []
    limit = max_attempts if max_attempts is not None else maximum * 20
    attempts = 0
    rejected = 0
    while len(candidates) < maximum and attempts < limit:
        attempts += 1
        candidate = BKTParams(round(rng.uniform(0.05, 0.95), 12), round(rng.uniform(0.001, 0.30), 12), round(rng.uniform(0.01, 0.40), 12), round(rng.uniform(0.01, 0.40), 12))
        if valid_params(candidate):
            candidates.append(candidate)
        else:
            rejected += 1
    stats = {"attempts": attempts, "acceptedCandidates": len(candidates), "rejectedCandidates": rejected, "maxCandidates": maximum, "maxAttempts": limit, "stopReason": "MAX_CANDIDATES_REACHED" if len(candidates) >= maximum else "MAX_ATTEMPTS_REACHED"}
    return (candidates, stats) if return_stats else candidates


def candidate_hash(candidates: list[BKTParams]) -> str:
    payload = json.dumps([asdict(candidate) for candidate in candidates], sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def select_params(candidates: list[BKTParams], events: list[tuple[str, str, int]]) -> tuple[BKTParams, float]:
    scores: list[tuple[float, int, BKTParams]] = []
    for index, candidate in enumerate(candidates):
        states: dict[tuple[str, str], float] = {}
        labels: list[int] = []
        predictions: list[float] = []
        for student, skill, correct in events:
            key = (student, skill)
            p_l = states.get(key, candidate.L0)
            predictions.append(bkt_predict(p_l, candidate))
            labels.append(correct)
            states[key] = bkt_update(p_l, correct, candidate)
        scores.append((logloss(labels, predictions), index, candidate))
    best = min(scores, key=lambda item: (item[0], item[1]))
    return best[2], best[0]


def eligible_skill_counts(counts: dict[str, int], threshold: int = MIN_SKILL_EVENTS) -> tuple[list[str], list[str]]:
    return (sorted(skill for skill, count in counts.items() if count >= threshold), sorted(skill for skill, count in counts.items() if count < threshold))


def git_ignored(path: Path) -> bool:
    return subprocess.run(["git", "check-ignore", "--quiet", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def git_committed(path: Path) -> bool:
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
    return tracked and subprocess.run(["git", "cat-file", "-e", f"HEAD:{path}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def environment() -> dict[str, str]:
    return {"pythonVersion": sys.version, "pythonImplementation": platform.python_implementation(), "os": platform.system(), "osRelease": platform.release(), "machine": platform.machine()}


def reproduce_command() -> str:
    return "mvn -B -ntp -Dtest=edu.f21.BktTest test -f server/pom.xml && PYTHONDONTWRITEBYTECODE=1 python3 -m scripts.fit_g5_bkt --clean data/processed/assistments-clean.jsonl --split data/processed/g5-split-assignments.jsonl --split-manifest data/manifests/g5-split-manifest.json --parameters-output data/processed/g5-bkt-parameters.json --report-output artifacts/g5-bkt-fit.json --java-parity-report server/target/surefire-reports/TEST-edu.f21.BktTest.xml"


def load_frozen_inputs(clean_path: Path, split_path: Path, manifest_path: Path) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[str, str]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("passed") is not True or manifest.get("invariants", {}).get("passed") is not True:
        raise ValueError("G5-02 split gate failed")
    clean_hash, _ = sha256_file(clean_path)
    split_hash, _ = sha256_file(split_path)
    if clean_hash != manifest.get("cleanSha256") or split_hash != manifest.get("splitAssignmentSha256"):
        raise ValueError("frozen input hash mismatch")
    assignments: dict[int, dict[str, Any]] = {}
    with split_path.open(encoding="utf-8") as handle:
        for line in handle:
            assignment = json.loads(line)
            assignments[int(assignment["sourceRow"])] = assignment
    events: list[dict[str, Any]] = []
    with clean_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            assignment = assignments.get(int(row["sourceRow"]))
            if assignment and assignment["partition"] in {"train", "validation"}:
                events.append({**row, "partition": assignment["partition"], "sequenceIndex": assignment["sequenceIndex"]})
    return events, assignments, {"rawSha256": manifest.get("inputRawSha256"), "cleanSha256": clean_hash, "splitSha256": split_hash, "partitionHashes": manifest.get("partitionHashes", {})}


def evaluate_validation(validation_events: list[dict[str, Any]], train_events: list[dict[str, Any]], params_by_skill: dict[str, BKTParams], fallback: BKTParams, known_skills: set[str]) -> dict[str, Any]:
    states: dict[tuple[str, str], float] = {}
    for row in sorted(train_events, key=lambda item: (item["studentExternalId"], item["sequenceIndex"], item["sourceRow"])):
        params = params_by_skill.get(row["skillExternalId"], fallback)
        key = (row["studentExternalId"], row["skillExternalId"])
        states[key] = bkt_update(states.get(key, params.L0), int(row["correct"]), params)
    labels: list[int] = []
    predictions: list[float] = []
    unknown = 0
    for row in sorted(validation_events, key=lambda item: (item["studentExternalId"], item["sequenceIndex"], item["sourceRow"])):
        params = params_by_skill.get(row["skillExternalId"], fallback)
        unknown += int(row["skillExternalId"] not in known_skills)
        key = (row["studentExternalId"], row["skillExternalId"])
        p_l = states.get(key, params.L0)
        predictions.append(bkt_predict(p_l, params))
        labels.append(int(row["correct"]))
        states[key] = bkt_update(p_l, int(row["correct"]), params)
    return {"targets": len(labels), "positive": sum(labels), "negative": len(labels) - sum(labels), "unknownSkillTargets": unknown, "unknownSkillRate": unknown / len(labels) if labels else None, "logLoss": logloss(labels, predictions) if labels else None}


def fit_experiment(events: list[dict[str, Any]], assignments: dict[int, dict[str, Any]], candidates: list[BKTParams]) -> dict[str, Any]:
    train_events = sorted((row for row in events if row["partition"] == "train"), key=lambda item: (item["studentExternalId"], item["sequenceIndex"], item["sourceRow"]))
    validation_events = sorted((row for row in events if row["partition"] == "validation"), key=lambda item: (item["studentExternalId"], item["sequenceIndex"], item["sourceRow"]))
    train_sources = {int(row["sourceRow"]) for row in train_events}
    validation_sources = {int(row["sourceRow"]) for row in validation_events}
    test_sources = {source for source, assignment in assignments.items() if assignment["partition"] == "test"}
    short_sources = {source for source, assignment in assignments.items() if assignment["partition"] == "SHORT_COVERAGE"}
    course_params, train_loss = select_params(candidates, [(row["studentExternalId"], row["skillExternalId"], int(row["correct"])) for row in train_events])
    skill_events: defaultdict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for row in train_events:
        skill_events[row["skillExternalId"]].append((row["studentExternalId"], row["skillExternalId"], int(row["correct"])))
    eligible, fallback_skills = eligible_skill_counts({skill: len(values) for skill, values in skill_events.items()})
    skill_params: dict[str, BKTParams] = {}
    skill_losses: dict[str, float] = {}
    for skill in eligible:
        skill_params[skill], skill_losses[skill] = select_params(candidates, skill_events[skill])
    train_skill_vocabulary = set(skill_events)
    strategies = {"COURSE_SHARED": evaluate_validation(validation_events, train_events, {}, course_params, train_skill_vocabulary), "SKILL_SPECIFIC_FALLBACK": evaluate_validation(validation_events, train_events, skill_params, course_params, train_skill_vocabulary)}
    selected = min(strategies, key=lambda name: (strategies[name]["logLoss"] if strategies[name]["logLoss"] is not None else float("inf"), name))
    return {"trainEvents": train_events, "validationEvents": validation_events, "trainSources": train_sources, "validationSources": validation_sources, "testSources": test_sources, "shortSources": short_sources, "courseParams": course_params, "trainLoss": train_loss, "skillParams": skill_params, "skillLosses": skill_losses, "eligible": eligible, "fallbackSkills": fallback_skills, "strategies": strategies, "selected": selected}


def _xml_parity(path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "javaVectorTestPresent": False, "javaPassed": False}
    if path is None or not path.exists():
        return result
    root = ET.parse(path).getroot()
    result.update({key: int(root.attrib.get(key, "0")) for key in ("tests", "failures", "errors", "skipped")})
    result["javaVectorTestPresent"] = any(case.attrib.get("name") == "sameReferenceVectorsAsPython" for case in root.iter("testcase"))
    result["javaPassed"] = result["tests"] > 0 and all(result[key] == 0 for key in ("failures", "errors", "skipped")) and result["javaVectorTestPresent"]
    return result


def cross_language_parity(project_root: Path, java_report: Path | None) -> dict[str, Any]:
    py_path = project_root / "tests/fixtures/bkt-vectors.json"
    java_path = project_root / "server/src/test/resources/bkt-vectors.json"
    py_hash, _ = sha256_file(py_path)
    java_hash, _ = sha256_file(java_path)
    py_vectors = json.loads(py_path.read_text(encoding="utf-8"))
    params = BKTParams(0.2, 0.1, 0.2, 0.1)
    errors = [abs(bkt_update(float(v["before"]), int(bool(v["correct"])), params) - float(v["expected"])) for v in py_vectors if float(v["weight"]) == 1.0]
    java = _xml_parity(java_report)
    report = {"pythonVectors": "tests/fixtures/bkt-vectors.json", "javaVectors": "server/src/test/resources/bkt-vectors.json", "pythonSha256": py_hash, "javaSha256": java_hash, "hashesMatch": py_hash == java_hash == VECTOR_HASH, "vectorCount": len(py_vectors), "g5WeightOneVectorCount": len(errors), "tolerance": 1e-10, "pythonMaxAbsoluteError": max(errors) if errors else None, "pythonPassed": bool(errors) and max(errors) <= 1e-10, "javaTestClass": "edu.f21.BktTest", "javaTestMethod": "sameReferenceVectorsAsPython", "javaTestCommand": "mvn -B -ntp -Dtest=edu.f21.BktTest test -f server/pom.xml", "surefire": java}
    report["passed"] = report["hashesMatch"] and report["vectorCount"] == 10 and report["g5WeightOneVectorCount"] == 9 and report["pythonPassed"] and java["javaPassed"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--parameters-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--java-parity-report", type=Path)
    args = parser.parse_args()
    candidates, candidate_stats = generate_candidates(SEED, MAX_CANDIDATES, return_stats=True)
    events, assignments, input_hashes = load_frozen_inputs(args.clean, args.split, args.split_manifest)
    result = fit_experiment(events, assignments, candidates)
    parameter_payload = {"schemaVersion": SCHEMA_VERSION, "courseShared": asdict(result["courseParams"]), "skillSpecific": {skill: asdict(params) for skill, params in sorted(result["skillParams"].items())}, "skillTrainLogLoss": result["skillLosses"]}
    parameter_bytes = (json.dumps(parameter_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    args.parameters_output.parent.mkdir(parents=True, exist_ok=True)
    args.parameters_output.write_bytes(parameter_bytes)
    parameter_hash = hashlib.sha256(parameter_bytes).hexdigest()
    processed_paths = [args.clean, args.split, args.parameters_output]
    processed_checks = {str(path): {"gitIgnored": git_ignored(path), "gitTracked": git_tracked(path), "committed": git_committed(path)} for path in processed_paths}
    params_valid = valid_params(result["courseParams"]) and all(valid_params(params) for params in result["skillParams"].values()) and all(valid_params(candidate) for candidate in candidates)
    fit_sources, selection_sources = result["trainSources"], result["validationSources"]
    test_sources, short_sources = result["testSources"], result["shortSources"]
    leakage = {"combinedCleanFileScanned": True, "testRowsMaterializedForModeling": False, "testLabelsUsedForFitting": False, "testLabelsUsedForValidation": False, "testLabelsUsedForStrategySelection": False, "testMetricsComputed": False, "testEvaluationStatus": "DEFERRED_TO_G5_04", "fitPartitions": sorted({row["partition"] for row in result["trainEvents"]}), "selectionPartitions": sorted({row["partition"] for row in result["validationEvents"]}), "fitEventCount": len(fit_sources), "selectionEventCount": len(selection_sources), "fitTestIntersectionCount": len(fit_sources & test_sources), "selectionTestIntersectionCount": len(selection_sources & test_sources), "fitShortCoverageIntersectionCount": len(fit_sources & short_sources), "selectionShortCoverageIntersectionCount": len(selection_sources & short_sources), "trainValidationTargetIntersectionCount": len(fit_sources & selection_sources), "validationStrategyTargetSetsMatch": result["strategies"]["COURSE_SHARED"]["targets"] == result["strategies"]["SKILL_SPECIFIC_FALLBACK"]["targets"] == len(selection_sources), "testMetricsCount": 0, "testPredictionCount": 0, "shortCoverageModelEventCount": len(short_sources & (fit_sources | selection_sources)), "fitParameterSourcePartitions": sorted({row["partition"] for row in result["trainEvents"]}), "allFitParametersFromTrain": bool(fit_sources) and sorted({row["partition"] for row in result["trainEvents"]}) == ["train"], "predictBeforeUpdate": True}
    leakage_passed = (leakage["fitPartitions"] == ["train"] and leakage["selectionPartitions"] == ["validation"] and leakage["fitTestIntersectionCount"] == 0 and leakage["selectionTestIntersectionCount"] == 0 and leakage["fitShortCoverageIntersectionCount"] == 0 and leakage["selectionShortCoverageIntersectionCount"] == 0 and leakage["trainValidationTargetIntersectionCount"] == 0 and leakage["validationStrategyTargetSetsMatch"] and leakage["testMetricsCount"] == 0 and leakage["testPredictionCount"] == 0 and leakage["shortCoverageModelEventCount"] == 0 and leakage["allFitParametersFromTrain"] and not leakage["testRowsMaterializedForModeling"] and not leakage["testLabelsUsedForFitting"] and not leakage["testLabelsUsedForValidation"] and not leakage["testLabelsUsedForStrategySelection"] and not leakage["testMetricsComputed"] and leakage["predictBeforeUpdate"])
    parity = cross_language_parity(Path.cwd(), args.java_parity_report)
    gates = {"inputHashesMatch": True, "g5_02Passed": True, "parametersValid": params_valid, "processedFilesIgnored": all(item["gitIgnored"] and not item["gitTracked"] and not item["committed"] for item in processed_checks.values()), "leakageAssertions": leakage_passed, "crossLanguageParity": parity["passed"]}
    report = {"passed": all(gates.values()), "verifiedAt": utc_now(), "gitCommit": git_commit(), "generator": {"script": "scripts/fit_g5_bkt.py", "schemaVersion": SCHEMA_VERSION}, "reproduceCommand": reproduce_command(), "environment": environment(), "schemaVersion": SCHEMA_VERSION, "modelVersion": "BKT_G5_FORMAL_V1", "formulaVersion": FORMULA_VERSION, "input": input_hashes, "rows": {"train": len(result["trainEvents"]), "validation": len(result["validationEvents"]), "test": len(test_sources), "SHORT_COVERAGE": len(short_sources), "trainTargets": len(result["trainEvents"]), "validationTargets": len(result["validationEvents"]), "testStatus": "DEFERRED_TO_G5_04"}, "candidateSpace": {"bounds": {"L0": [0.05, 0.95], "T": [0.001, 0.30], "G": [0.01, 0.40], "S": [0.01, 0.40], "constraint": "G + S < 1"}, "seed": SEED, **candidate_stats, "candidateHash": candidate_hash(candidates), "selectionTarget": "train LogLoss"}, "courseShared": {"trainLogLoss": result["trainLoss"], "parameterHash": hashlib.sha256(json.dumps(asdict(result["courseParams"]), sort_keys=True, separators=(",", ":")).encode()).hexdigest()}, "skillSpecific": {"eligibleSkills": len(result["eligible"]), "fallbackSkills": len(result["fallbackSkills"]), "parameterFile": {"path": str(args.parameters_output), "sha256": parameter_hash, "bytes": len(parameter_bytes), "gitIgnored": processed_checks[str(args.parameters_output)]["gitIgnored"], "gitTracked": processed_checks[str(args.parameters_output)]["gitTracked"], "committed": processed_checks[str(args.parameters_output)]["committed"]}}, "validationStrategies": result["strategies"], "selectedStrategyVersion": result["selected"], "leakageAssertions": leakage, "crossLanguageParity": parity, "gates": gates, "processedFiles": processed_checks, "errors": [] if all(gates.values()) else [name for name, value in gates.items() if not value]}
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "selectedStrategyVersion": result["selected"], "candidateHash": report["candidateSpace"]["candidateHash"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

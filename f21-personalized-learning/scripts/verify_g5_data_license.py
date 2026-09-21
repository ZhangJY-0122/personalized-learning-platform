#!/usr/bin/env python3
"""Validate the G5-01A source manifest and generate its audit artifact.

This gate is deliberately offline: it validates evidence recorded from the
official pages and never downloads or reads the dataset itself.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OFFICIAL_PAGE = (
    "https://sites.google.com/site/assistmentsdata/home/2009-2010-assistment-data/"
    "skill-builder-data-2009-2010"
)
TERMS_PAGE = "https://sites.google.com/site/assistmentstestbed/4-analyze-data/terms-of-use-for-data"
USAGE_BASIS = "OFFICIAL_RESEARCH_USE_TERMS"
MULTI_SKILL_POLICY = "COMPOSITE_TOKEN_NO_SPLIT"
REQUIRED_CITATIONS = 2


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if manifest.get("datasetName") != "ASSISTments 2009-2010 Skill Builder":
        errors.append("datasetName must identify ASSISTments 2009-2010 Skill Builder")
    if manifest.get("datasetVersion") != "2009-2010 Skill Builder corrected version":
        errors.append("datasetVersion must be the corrected version")
    if not manifest.get("correctedFileId"):
        errors.append("correctedFileId is required")
    if manifest.get("officialPage") != OFFICIAL_PAGE:
        errors.append("officialPage is missing or is not the official data page")
    if manifest.get("termsPage") != TERMS_PAGE:
        errors.append("termsPage is missing or is not the official terms page")

    dates = manifest.get("accessDates")
    if not isinstance(dates, dict) or not dates.get("officialPage") or not dates.get("termsPage"):
        errors.append("accessDates for both official pages are required")

    if manifest.get("standardLicense", object()) is not None:
        errors.append("standardLicense must be null; the pages do not grant an SPDX license")
    if manifest.get("usageBasis") != USAGE_BASIS:
        errors.append("usageBasis must be OFFICIAL_RESEARCH_USE_TERMS")
    citations = manifest.get("citationRequirements")
    if not isinstance(citations, list) or len(citations) < REQUIRED_CITATIONS:
        errors.append("citationRequirements must record the official URL and ASSISTments paper")

    restrictions = manifest.get("restrictions")
    if not isinstance(restrictions, dict):
        errors.append("restrictions evidence is required")
    else:
        if restrictions.get("noReidentification") is not True:
            errors.append("noReidentification restriction is not evidenced")
        if restrictions.get("noThirdPartyRedistribution") is not True:
            errors.append("noThirdPartyRedistribution restriction is not evidenced")
        if restrictions.get("noCommercialization") is not True:
            errors.append("noCommercialization restriction is not evidenced")

    if manifest.get("redistributionAllowed") is not False:
        errors.append("redistributionAllowed must be false")
    if manifest.get("commercialUseAllowed") is not False:
        errors.append("commercialUseAllowed must be false")
    if manifest.get("reidentificationAllowed") is not False:
        errors.append("reidentificationAllowed must be false")
    if manifest.get("rawDataDownloaded") is not False:
        errors.append("rawDataDownloaded must be false for G5-01A")
    if manifest.get("multiSkillPolicy") != MULTI_SKILL_POLICY:
        errors.append("multiSkillPolicy must be COMPOSITE_TOKEN_NO_SPLIT")

    evidence = manifest.get("sourceEvidence")
    evidence_fields = (
        "officialPageConfirmsCorrectedVersion",
        "officialPageConfirmsOneRowPerStudentProblem",
        "officialPageConfirmsCompositeSkillToken",
        "officialPageRequiresExactDataPageCitation",
        "termsPageConfirmsNoReidentification",
        "termsPageConfirmsNoThirdPartyRedistribution",
        "termsPageConfirmsNoCommercialization",
    )
    if not isinstance(evidence, dict) or any(evidence.get(field) is not True for field in evidence_fields):
        errors.append("official source and terms evidence is incomplete")

    sample = manifest.get("sampleEvidence")
    if (
        not isinstance(sample, dict)
        or sample.get("scope") != "PRE_G5_FIRST_1MIB_SAMPLE_ONLY"
        or sample.get("formalExperimentEligible") is not False
        or sample.get("mappingExcludedFromFormalExperiment")
        != "split underscore / deterministic primary skill"
    ):
        errors.append("public-data-inspection.json must remain pre-G5 sample evidence only")

    if manifest.get("errors") not in ([], None):
        errors.extend(str(item) for item in manifest["errors"])
    return errors


def current_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def build_artifact(manifest: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    return {
        "passed": not errors,
        "verifiedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "gitCommit": current_git_commit(),
        "datasetName": manifest.get("datasetName"),
        "datasetVersion": manifest.get("datasetVersion"),
        "correctedFileId": manifest.get("correctedFileId"),
        "officialPage": manifest.get("officialPage"),
        "termsPage": manifest.get("termsPage"),
        "accessDates": manifest.get("accessDates"),
        "standardLicense": manifest.get("standardLicense"),
        "usageBasis": manifest.get("usageBasis"),
        "citationRequirements": manifest.get("citationRequirements"),
        "restrictions": manifest.get("restrictions"),
        "redistributionAllowed": manifest.get("redistributionAllowed"),
        "commercialUseAllowed": manifest.get("commercialUseAllowed"),
        "reidentificationAllowed": manifest.get("reidentificationAllowed"),
        "rawDataDownloaded": manifest.get("rawDataDownloaded"),
        "multiSkillPolicy": manifest.get("multiSkillPolicy"),
        "sampleEvidence": manifest.get("sampleEvidence"),
        "sourceEvidence": manifest.get("sourceEvidence"),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        manifest = {}
        errors = [f"cannot read manifest: {exc}"]
    else:
        errors = validate_manifest(manifest)

    artifact = build_artifact(manifest, errors)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": artifact["passed"], "errors": errors}, ensure_ascii=False))
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

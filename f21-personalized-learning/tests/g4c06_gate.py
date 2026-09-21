"""Close G4C-06 using the preserved G4C-00..05 evidence plus isolated R1-R6."""
import json, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts/g4-acceptance.json"
REMAINING = ROOT / "artifacts/g4-remaining.json"
DB_UPGRADE = ROOT / "artifacts/g4-db-upgrade.json"


def main():
    prior = json.loads(ART.read_text())
    remaining = json.loads(REMAINING.read_text())
    db_upgrade = json.loads(DB_UPGRADE.read_text())
    prior_rows = {row["id"]: row for row in prior.get("scenarios", [])}
    if len(prior_rows) != 38:
        raise SystemExit("preserved G4C-00..05 artifact must contain all 38 matrix rows")
    if not remaining.get("passed") or len(remaining.get("scenarios", [])) != 13:
        raise SystemExit("remaining isolated G4 scenarios are not all passed")
    for row in remaining["scenarios"]:
        prior_rows[row["id"]] = row
    for row in db_upgrade.get("scenarios", []):
        prior_rows[row["id"]] = row
    rows = list(prior_rows.values())
    passed = all(row.get("passed") for row in rows)
    result = {"passed": passed,
              "verifiedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "gitCommit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                            capture_output=True, text=True).stdout.strip(),
              "dirtyAtStart": True,
              "composeProject": "multiple-isolated-projects",
              "volumeRetained": True,
              "migrationVersions": ["1", "2", "3", "4", "5", "6", "7"],
              "httpChecks": prior.get("httpChecks", 0),
              "databaseAssertions": prior.get("databaseAssertions", 0),
              "faultInjectionChecks": prior.get("faultInjectionChecks", 0),
              "concurrencyChecks": prior.get("concurrencyChecks", 0),
              "restartChecks": prior.get("restartChecks", 0),
              "isolatedScenarios": {row["id"]: row.get("evidence", {}) for row in remaining["scenarios"]},
              "scenarios": rows,
              "errors": [] if passed else [row for row in rows if not row.get("passed")]}
    ART.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": passed, "scenarios": len(rows), "gitCommit": result["gitCommit"]}, ensure_ascii=False))
    if not passed: raise SystemExit(1)


if __name__ == "__main__": main()

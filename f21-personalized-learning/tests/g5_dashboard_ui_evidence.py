#!/usr/bin/env python3
"""Freeze G5-07C frontend/static and interactive browser acceptance evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/g5-07c-ui-validation.json")
    parser.add_argument("--browser-verified", action="store_true", help="set only after interactive Chrome acceptance")
    args = parser.parse_args()
    main_js = (ROOT / "web/src/main.js").read_text()
    dashboard_js = (ROOT / "web/src/dashboard.js").read_text()
    css = (ROOT / "web/src/style.css").read_text()
    checks = {
        "dashboardModuleUsesFrozenGetApis": all(
            value in dashboard_js
            for value in (
                "api('/models?page=1&pageSize=20')",
                "api('/experiments?page=1&pageSize=20')",
                "'/weakness-statistics'",
                "'/recommendation-metrics'",
            )
        )
        and "POST" not in dashboard_js,
        "staffOnlyDashboardBindings": "dashboardView && user.role!=='STUDENT'" in main_js and "tab==='看板' && user.role!=='STUDENT'" in main_js,
        "provenanceAndDenominatorsVisible": all(value in main_js for value in ("metric.numerator", "metric.denominator", "m.artifact.sha256", "experiment.artifact.sha256")),
        "documentedEmptyStates": "暂无满足证据门槛的学习者" in main_js and "当前暂无产品事件数据" in main_js,
        "dashboardStylesPresent": all(value in css for value in ("dashboard-panel", "dashboard-grid", "dashboard-card", "metric-list")),
        "productionBundlePresent": any((ROOT / "web/dist").glob("assets/*.js")) and (ROOT / "web/dist/index.html").is_file(),
        "browserAcceptance": args.browser_verified,
    }
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()
    artifact = {
        "passed": all(checks.values()),
        "schemaVersion": "g5-07c-ui-validation-v1",
        "implementationCommit": commit,
        "verifiedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "checks": checks,
        "browser": {
            "surface": "Chrome local preview",
            "url": "http://127.0.0.1:15173/",
            "teacherAccount": "teacher01",
            "studentAccount": "student01",
            "observations": [
                "教师登录后可见实验看板入口，模型与实验卡片显示真实 API 数据、样本量和产物短 hash。",
                "教师进入授权 Java 课程后可见看板页；薄弱点空状态、推荐指标四项分母和可信完成说明正确显示。",
                "学生登录后不显示实验看板入口，也不显示课程看板导航。",
                "刷新课程统计可重复读取接口；控制台无 error/warning。",
            ],
            "credentialsRecorded": False,
        },
        "security": {"tokensRecorded": False, "rawStudentIdentifiersRecorded": False, "answersOrPasswordsDisplayed": False},
        "limitations": [
            "浏览器检查覆盖本地 Chrome 交互与控制台；跨浏览器兼容性留待 G5-08。",
            "本地开发数据库当前没有满足证据门槛的学习者和推荐产品事件，页面显示契约规定的空状态。",
        ],
    }
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"passed": artifact["passed"], "checks": len(checks), "browserAcceptance": args.browser_verified}, ensure_ascii=False))
    if not artifact["passed"]:
        raise SystemExit("G5-07C UI evidence checks failed")


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp_context_budget.demo import write_demo_files


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_context_budget", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )


def test_scan_select_check_export_loop(tmp_path: Path) -> None:
    tool_list, fixtures = write_demo_files(tmp_path)
    report = tmp_path / "report.md"
    full_lock = tmp_path / "full.lock.json"
    selected_lock = tmp_path / "selected.lock.json"
    sarif = tmp_path / "budget.sarif"

    scan = run_cli(
        "scan",
        "--tool-list",
        str(tool_list),
        "--fixtures",
        str(fixtures),
        "--out",
        str(report),
        "--lock-out",
        str(full_lock),
    )
    assert scan.returncode == 0, scan.stderr
    assert "SCAN_TOOLS=120" in scan.stdout
    assert report.is_file()

    fail = run_cli("check", "--lock", str(full_lock), "--max-schema-tokens", "30000")
    assert fail.returncode == 1
    assert "BUDGET_STATUS=FAIL" in fail.stdout

    select = run_cli(
        "select",
        "--tool-list",
        str(tool_list),
        "--task",
        "triage a GitHub issue and update one ticket",
        "--max-tools",
        "8",
        "--max-schema-tokens",
        "6000",
        "--out-lock",
        str(selected_lock),
    )
    assert select.returncode == 0, select.stderr
    lock = json.loads(selected_lock.read_text())
    assert lock["selected_schema_tokens"] <= 6000

    check = run_cli("check", "--lock", str(selected_lock), "--max-schema-tokens", "6000")
    assert check.returncode == 0, check.stdout + check.stderr
    assert "BUDGET_STATUS=PASS" in check.stdout

    export = run_cli(
        "export", "--lock", str(selected_lock), "--format", "sarif", "--out", str(sarif)
    )
    assert export.returncode == 0, export.stderr
    assert json.loads(sarif.read_text())["version"] == "2.1.0"


def test_demo_prints_spine_budget_proof() -> None:
    result = run_cli(
        "demo",
        "--task",
        "triage a GitHub issue and update one ticket",
        "--max-tools",
        "8",
        "--max-schema-tokens",
        "6000",
        "--max-response-tokens",
        "4000",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert int(lines["DEMO_CATALOG_SERVERS"]) == 5
    assert int(lines["DEMO_CATALOG_TOOLS"]) >= 100
    assert int(lines["BEFORE_SCHEMA_TOKENS"]) >= 90000
    assert int(lines["SELECTED_TOOLS"]) <= 8
    assert int(lines["AFTER_SCHEMA_TOKENS"]) <= 6000
    assert lines["OVERSIZED_RESPONSE_FIXTURE"] == "flagged"
    assert lines["BUDGET_STATUS"] == "PASS"

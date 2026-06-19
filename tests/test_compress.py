from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp_context_budget.tokens import estimate_tokens


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_context_budget", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def test_compress_responses_writes_valid_under_cap_fixture(tmp_path: Path) -> None:
    fixtures = tmp_path / "responses"
    fixtures.mkdir()
    fixture = fixtures / "issue.json"
    fixture.write_text(
        json.dumps(
            {
                "tool": "github/get_issue",
                "response": {
                    "id": 42,
                    "title": "Parser bug",
                    "url": "https://example.invalid/issues/42",
                    "state": "open",
                    "body": "stack trace and reproduction steps " * 120,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "compressed"
    report = tmp_path / "compression-report.json"

    result = run_cli(
        "compress-responses",
        "--fixtures",
        str(fixtures),
        "--max-response-tokens",
        "120",
        "--out-dir",
        str(out_dir),
        "--report",
        str(report),
        "--keep-fields",
        "id,title,url,state,summary",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    compressed = json.loads((out_dir / "issue.json").read_text(encoding="utf-8"))
    response = compressed["response"]
    assert set(response) == {"id", "title", "url", "state", "summary"}
    assert estimate_tokens(json.dumps(response, sort_keys=True)) <= 120
    rows = json.loads(report.read_text(encoding="utf-8"))["files"]
    assert rows[0]["status"] == "COMPRESSED"
    assert rows[0]["after_response_tokens"] <= 120
    assert "COMPRESSION_STATUS=PASS" in result.stdout


def test_compress_responses_reports_small_fixture_as_skipped(tmp_path: Path) -> None:
    fixture = tmp_path / "small.json"
    fixture.write_text(json.dumps({"tool": "x/y", "response": {"ok": True}}) + "\n")
    out_dir = tmp_path / "out"
    report = tmp_path / "report.json"

    result = run_cli(
        "compress-responses",
        "--fixtures",
        str(fixture),
        "--max-response-tokens",
        "100",
        "--out-dir",
        str(out_dir),
        "--report",
        str(report),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((out_dir / "small.json").read_text())["response"] == {"ok": True}
    assert json.loads(report.read_text())["files"][0]["status"] == "SKIPPED_UNDER_CAP"


def test_compress_demo_prints_before_after_proof() -> None:
    result = run_cli("compress-demo", "--max-response-tokens", "4000")

    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert int(lines["BEFORE_RESPONSE_TOKENS"]) > 4000
    assert int(lines["AFTER_RESPONSE_TOKENS"]) <= 4000
    assert lines["COMPRESSION_STATUS"] == "PASS"

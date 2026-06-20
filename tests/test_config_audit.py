from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp_context_budget.config_audit import audit_config


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_context_budget", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def test_config_audit_detects_plaintext_without_printing_value(tmp_path: Path) -> None:
    literal = "plaintext-demo-token-1234567890"
    config = tmp_path / "mcp.json"
    report = tmp_path / "audit.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "command": "github-mcp",
                        "env": {"GITHUB_TOKEN": literal},
                        "args": ["--token", literal],
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_cli(
        "config-audit",
        "--config",
        str(config),
        "--json-out",
        str(report),
        "--fail-on",
        "none",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFIG_AUDIT_FINDINGS=" in result.stdout
    serialized = report.read_text(encoding="utf-8") + result.stdout + result.stderr
    assert literal not in serialized
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["counts"]["high"] >= 1
    assert {"path", "severity", "secret_class", "length_bucket", "fingerprint"} <= set(
        payload["findings"][0]
    )


def test_config_audit_fail_on_high_exits_nonzero(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"x": {"env": {"API_TOKEN": "plaintext-demo-token-abcdef"}}}})
        + "\n",
        encoding="utf-8",
    )

    result = run_cli("config-audit", "--config", str(config), "--fail-on", "high")

    assert result.returncode == 1
    assert "CONFIG_AUDIT_STATUS=FAIL" in result.stdout


def test_config_audit_ignores_safe_references(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "env": {
                            "GITHUB_TOKEN": "${GITHUB_TOKEN}",
                            "LINEAR_API_KEY": "op://vault/item/field",
                        }
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = audit_config(config)

    assert report["counts"] == {"total": 0, "high": 0}


def test_config_audit_invalid_json_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text("{not json", encoding="utf-8")

    result = run_cli("config-audit", "--config", str(config))

    assert result.returncode == 2
    assert "invalid JSON" in result.stderr


def test_config_audit_demo_proves_redaction_and_safe_reference_handling() -> None:
    result = run_cli("config-audit-demo")

    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert int(lines["CONFIG_AUDIT_FINDINGS"]) >= 1
    assert lines["CONFIG_AUDIT_SECRET_VALUES_REDACTED"] == "true"
    assert lines["CONFIG_AUDIT_SAFE_REFERENCE_IGNORED"] == "true"
    assert lines["CONFIG_AUDIT_STATUS"] == "PASS"

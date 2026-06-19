from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_context_budget", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def write_config_and_lock(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "command": "github-mcp",
                        "env": {"GITHUB_TOKEN": "should-not-leak"},
                        "tools": [
                            {"name": "get_issue", "description": "Get issue"},
                            {"name": "delete_repo", "description": "Delete repository"},
                        ],
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    lock = tmp_path / "mcp-budget.lock.json"
    lock.write_text(
        json.dumps({"selected_tools": ["github/get_issue"]}) + "\n",
        encoding="utf-8",
    )
    return config, lock


def test_config_apply_dry_run_writes_patch_without_changing_config(tmp_path: Path) -> None:
    config, lock = write_config_and_lock(tmp_path)
    before = config.read_text(encoding="utf-8")
    patch = tmp_path / "patch.json"

    result = run_cli(
        "config-apply",
        "--config",
        str(config),
        "--lock",
        str(lock),
        "--mode",
        "disable-unselected",
        "--dry-run",
        "--patch-out",
        str(patch),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_text(encoding="utf-8") == before
    report = json.loads(patch.read_text(encoding="utf-8"))
    assert report["dry_run"] is True
    assert report["actions"] == [
        {"action": "disable_tool", "server": "github", "tool": "delete_repo", "target": "inline"}
    ]
    assert "should-not-leak" not in json.dumps(report)
    # single inline server, nothing command-discovered -> fully enforceable
    assert "CONFIG_APPLY_STATUS=PASS" in result.stdout


def test_config_apply_write_creates_backup_and_disables_only_unselected(tmp_path: Path) -> None:
    config, lock = write_config_and_lock(tmp_path)
    backup_dir = tmp_path / "backups"

    result = run_cli(
        "config-apply",
        "--config",
        str(config),
        "--lock",
        str(lock),
        "--mode",
        "disable-unselected",
        "--write",
        "--backup-dir",
        str(backup_dir),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(config.read_text(encoding="utf-8"))
    tools = payload["mcpServers"]["github"]["tools"]
    assert tools[0].get("enabled", True) is True
    assert tools[1]["enabled"] is False
    assert list(backup_dir.glob("mcp.json.*.bak"))
    assert "CONFIG_WRITE_BACKUP_CREATED=true" in result.stdout


def test_config_apply_fails_closed_on_malformed_config(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text(json.dumps({"notMcpServers": {}}) + "\n", encoding="utf-8")
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"selected_tools": []}) + "\n", encoding="utf-8")

    result = run_cli("config-apply", "--config", str(config), "--lock", str(lock))

    assert result.returncode == 2
    assert "MCP config must contain an `mcpServers` object" in result.stderr


def test_config_demo_prints_safe_apply_proof() -> None:
    result = run_cli("config-demo")

    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert int(lines["CONFIG_PATCH_ACTIONS"]) > 0
    assert lines["CONFIG_DRY_RUN_UNCHANGED"] == "true"
    assert lines["CONFIG_WRITE_BACKUP_CREATED"] == "true"
    # v0.2 contract: lock is fingerprint-bound, the external toolsListPath file is patched,
    # a command-discovered server is honestly reported PARTIAL (never a false PASS), and the
    # disable actually takes effect on rescan.
    assert lines["CONFIG_FINGERPRINT_MATCH"] == "true"
    assert int(lines["CONFIG_EXTERNAL_PATCHED"]) >= 1
    assert int(lines["CONFIG_NOT_PATCHABLE"]) >= 1
    assert lines["CONFIG_ENFORCED_ON_RESCAN"] == "true"
    assert lines["CONFIG_APPLY_STATUS"] == "PARTIAL"

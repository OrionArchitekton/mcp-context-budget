from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_context_budget.live_stdio import introspect_server_tools


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_context_budget", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def _fixture_args(*extra: str) -> list[str]:
    return ["-m", "mcp_context_budget", "_fixture-mcp-server", *extra]


def test_allow_start_lists_fixture_tools() -> None:
    result = introspect_server_tools(
        server="fixture",
        command=sys.executable,
        args=_fixture_args(),
        env={},
        start_timeout_seconds=2,
        max_stdio_bytes=65536,
    )

    assert {tool["name"] for tool in result.tools} == {"safe_read", "danger_delete"}


def test_allow_start_auto_falls_back_to_content_length_fixture() -> None:
    result = introspect_server_tools(
        server="fixture",
        command=sys.executable,
        args=_fixture_args("--mode", "content-length"),
        env={},
        start_timeout_seconds=0.2,
        max_stdio_bytes=65536,
    )

    assert {tool["name"] for tool in result.tools} == {"safe_read", "danger_delete"}


def test_allow_start_explicit_content_length_fixture() -> None:
    result = introspect_server_tools(
        server="fixture",
        command=sys.executable,
        args=_fixture_args("--mode", "content-length"),
        env={},
        start_timeout_seconds=2,
        max_stdio_bytes=65536,
        stdio_framing="content-length",
    )

    assert {tool["name"] for tool in result.tools} == {"safe_read", "danger_delete"}


def test_allow_start_times_out_hanging_server() -> None:
    with pytest.raises(ValueError, match="timed out"):
        introspect_server_tools(
            server="fixture",
            command=sys.executable,
            args=_fixture_args("--mode", "hang"),
            env={},
            start_timeout_seconds=0.1,
            max_stdio_bytes=65536,
        )


def test_allow_start_fails_closed_on_garbage_json() -> None:
    with pytest.raises(ValueError, match=r"Content-Length|JSON|headers"):
        introspect_server_tools(
            server="fixture",
            command=sys.executable,
            args=_fixture_args("--mode", "garbage"),
            env={},
            start_timeout_seconds=2,
            max_stdio_bytes=65536,
        )


def test_allow_start_enforces_max_stdio_bytes() -> None:
    with pytest.raises(ValueError, match="byte limit"):
        introspect_server_tools(
            server="fixture",
            command=sys.executable,
            args=_fixture_args("--mode", "large"),
            env={},
            start_timeout_seconds=2,
            max_stdio_bytes=256,
        )


def test_allow_start_scan_redacts_env_even_when_server_writes_stderr(tmp_path: Path) -> None:
    secret = "plain-demo-env-secret-123456"
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fixture": {
                        "command": sys.executable,
                        "args": _fixture_args("--mode", "stderr-secret"),
                        "env": {"DEMO_SECRET": secret},
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_cli("scan", "--config", str(config), "--allow-start")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SCAN_TOOLS=2" in result.stdout
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_allow_start_demo_proves_materialized_enforcement() -> None:
    result = run_cli("allow-start-demo", "--start-timeout-seconds", "2")

    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert lines["ALLOW_START_FIXTURE_SERVER"] == "started"
    assert lines["BEFORE_CONFIG_NOT_PATCHABLE"] == "1"
    assert int(lines["LIVE_TOOLS_LISTED"]) >= 2
    assert lines["MATERIALIZED_TOOL_LIST"] == "true"
    assert lines["AFTER_CONFIG_NOT_PATCHABLE"] == "0"
    assert lines["LIVE_INTROSPECTION_STATUS"] == "PASS"

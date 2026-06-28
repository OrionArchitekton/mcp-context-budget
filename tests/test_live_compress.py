from __future__ import annotations

import subprocess
import sys

import pytest

from mcp_context_budget.compress import compress_sampled_live_response, run_live_compress_demo
from mcp_context_budget.live_stdio import sample_live_tool_response


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_context_budget", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def _fixture_args(*extra: str) -> list[str]:
    return ["-m", "mcp_context_budget", "_fixture-mcp-server", *extra]


def test_sample_live_tool_response_returns_oversized_payload() -> None:
    payload = sample_live_tool_response(
        server="fixture",
        command=sys.executable,
        args=_fixture_args("--mode", "oversized-call"),
        env={},
        tool_name="safe_read",
        tool_arguments={"path": "demo"},
        start_timeout_seconds=2,
        max_stdio_bytes=65536,
    )

    assert isinstance(payload, dict)
    assert payload.get("id") == 42
    assert "oversized live tool response" in str(payload.get("summary", ""))


def test_compress_sampled_live_response_fits_cap() -> None:
    payload = sample_live_tool_response(
        server="fixture",
        command=sys.executable,
        args=_fixture_args("--mode", "oversized-call"),
        env={},
        tool_name="safe_read",
        tool_arguments={"path": "demo"},
        start_timeout_seconds=2,
        max_stdio_bytes=65536,
    )
    result = compress_sampled_live_response(payload, max_response_tokens=4000)

    assert result["before_response_tokens"] > 4000
    assert result["after_response_tokens"] <= 4000
    assert result["was_compressed"] is True
    assert result["status"] == "PASS"


def test_sample_live_tool_response_fails_closed_on_unknown_tool() -> None:
    with pytest.raises(ValueError, match="error"):
        sample_live_tool_response(
            server="fixture",
            command=sys.executable,
            args=_fixture_args("--mode", "oversized-call"),
            env={},
            tool_name="danger_delete",
            tool_arguments={"path": "demo"},
            start_timeout_seconds=2,
            max_stdio_bytes=65536,
        )


def test_live_compress_demo_prints_proof_lines() -> None:
    result = run_cli("live-compress-demo", "--max-response-tokens", "4000")

    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert int(lines["LIVE_RESPONSE_BEFORE_TOKENS"]) > int(lines["LIVE_RESPONSE_AFTER_TOKENS"])
    assert lines["LIVE_RESPONSE_COMPRESSED"] == "true"
    assert lines["LIVE_RESPONSE_COMPRESSION_STATUS"] == "PASS"


def test_run_live_compress_demo_returns_pass() -> None:
    result = run_live_compress_demo(max_response_tokens=4000, start_timeout_seconds=2)

    assert result["live_response_compression_status"] == "PASS"
    assert result["was_compressed"] is True

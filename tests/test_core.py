from __future__ import annotations

import json
from pathlib import Path

from mcp_context_budget.budget import check_lock, load_response_fixtures, scan_records
from mcp_context_budget.demo import GOLDEN_TOOLS, write_demo_files
from mcp_context_budget.loaders import load_mcp_config, load_tool_list
from mcp_context_budget.models import ToolRecord
from mcp_context_budget.selector import select_tools
from mcp_context_budget.tokens import estimate_tokens


def test_mcp_config_parser_preserves_servers_and_redacts_env(tmp_path: Path) -> None:
    tools = tmp_path / "tools.json"
    tools.write_text(
        json.dumps({"tools": [{"name": "get_issue", "description": "Get issue"}]}),
        encoding="utf-8",
    )
    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "command": "github-mcp",
                        "env": {"GITHUB_TOKEN": "not-a-real-token"},
                        "toolsListPath": str(tools),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    records, manifest = load_mcp_config(cfg)
    assert [record.server for record in records] == ["github"]
    assert manifest["servers"]["github"]["env"] == {"GITHUB_TOKEN": "<redacted>"}
    assert "not-a-real-token" not in json.dumps(manifest)


def test_tool_schema_hash_is_stable_across_json_key_order() -> None:
    a = ToolRecord("s", "t", "desc", {"type": "object", "properties": {"a": {"type": "string"}}})
    b = ToolRecord("s", "t", "desc", {"properties": {"a": {"type": "string"}}, "type": "object"})
    assert a.schema_hash == b.schema_hash


def test_token_estimator_is_deterministic() -> None:
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("abcde") == estimate_tokens("abcde")


def test_selector_returns_task_relevant_top_tools() -> None:
    records = [
        ToolRecord("calendar", "bulk_export", "Export everything", {"x": "large " * 100}),
        ToolRecord("github", "get_issue", "Get a GitHub issue for triage", {"x": "small"}),
        ToolRecord("linear", "update_ticket", "Update one ticket", {"x": "small"}),
    ]
    selected = select_tools(
        records,
        task="triage a GitHub issue and update one ticket",
        max_tools=2,
        max_schema_tokens=1000,
    )
    assert [tool.tool_id for tool in selected] == ["github/get_issue", "linear/update_ticket"]


def test_response_fixture_budgeter_flags_oversized_output(tmp_path: Path) -> None:
    fixture = tmp_path / "response.json"
    fixture.write_text(json.dumps({"tool": "github/get_issue", "response": "x" * 2000}))
    flags = load_response_fixtures(fixture, max_response_tokens=100)
    assert flags
    assert flags[0]["tool"] == "github/get_issue"


def test_core_failure_mode_full_catalog_fails_then_selected_catalog_passes(tmp_path: Path) -> None:
    tool_list, fixtures = write_demo_files(tmp_path)
    records = load_tool_list(tool_list)
    scan = scan_records(records)
    full_lock = scan.to_lock()
    assert full_lock["total_schema_tokens"] >= 90000
    full_passed, full_violations = check_lock(full_lock, max_schema_tokens=30000)
    assert not full_passed
    assert full_violations

    selected = select_tools(
        records,
        task="triage a GitHub issue and update one ticket",
        max_tools=8,
        max_schema_tokens=6000,
    )
    selected_lock = scan.to_lock(selected_tools=selected)
    selected_lock["response_fixture_flags"] = load_response_fixtures(
        fixtures, max_response_tokens=4000
    )
    passed, violations = check_lock(
        selected_lock,
        max_schema_tokens=6000,
        max_response_tokens=999999,
        require_tools=list(GOLDEN_TOOLS[:3]),
    )
    assert passed, violations
    assert selected_lock["selected_schema_tokens"] <= 6000
    assert set(GOLDEN_TOOLS[:3]).issubset(selected_lock["selected_tools"])

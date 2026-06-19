from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from mcp_context_budget.budget import check_lock, load_response_fixtures, scan_records
from mcp_context_budget.loaders import load_tool_list
from mcp_context_budget.selector import select_tools

SERVERS = ("github", "linear", "slack", "calendar", "atlassian")
GOLDEN_TOOLS = (
    "github/get_issue",
    "github/list_issue_comments",
    "github/update_issue",
    "linear/search_ticket",
    "linear/update_ticket",
)


def _tool(name: str, description: str, *, relevant: bool, index: int) -> dict[str, Any]:
    if relevant:
        schema_blob = "concise issue ticket triage schema " * 30
    else:
        schema_blob = ("large enterprise MCP schema with many rarely used parameters " * 260) + str(
            index
        )
    return {
        "name": name,
        "description": description,
        "tags": ["triage", "issue"] if relevant else ["bulk", "admin"],
        "inputSchema": {
            "type": "object",
            "description": schema_blob,
            "properties": {
                f"field_{i}": {"type": "string", "description": schema_blob}
                for i in range(1 if relevant else 3)
            },
        },
    }


def demo_catalog() -> dict[str, Any]:
    servers = []
    relevant_by_server = {
        "github": [
            ("get_issue", "Get a GitHub issue for triage"),
            ("list_issue_comments", "List GitHub issue comments"),
            ("update_issue", "Update a GitHub issue after triage"),
        ],
        "linear": [
            ("search_ticket", "Search an issue tracking ticket"),
            ("update_ticket", "Update one ticket with triage outcome"),
        ],
    }
    index = 0
    for server in SERVERS:
        tools = []
        for name, desc in relevant_by_server.get(server, []):
            tools.append(_tool(name, desc, relevant=True, index=index))
            index += 1
        while len(tools) < 24:
            tools.append(
                _tool(
                    f"{server}_bulk_admin_{len(tools):02d}",
                    f"Bulk administrative export and account operation for {server}",
                    relevant=False,
                    index=index,
                )
            )
            index += 1
        servers.append({"name": server, "tools": tools})
    return {"servers": servers}


def write_demo_files(root: Path) -> tuple[Path, Path]:
    tool_list = root / "demo-tools.json"
    fixtures = root / "responses"
    fixtures.mkdir(parents=True, exist_ok=True)
    tool_list.write_text(json.dumps(demo_catalog(), indent=2) + "\n", encoding="utf-8")
    oversized = {
        "tool": "github/get_issue",
        "response": {"body": "oversized issue response payload " * 2500},
    }
    (fixtures / "github_get_issue.json").write_text(json.dumps(oversized) + "\n", encoding="utf-8")
    return tool_list, fixtures


def run_demo(
    *, task: str, max_tools: int, max_schema_tokens: int, max_response_tokens: int
) -> dict:
    with tempfile.TemporaryDirectory(prefix="mcp-context-budget-demo-") as tmp:
        tool_list, fixtures = write_demo_files(Path(tmp))
        tools = load_tool_list(tool_list)
        scan = scan_records(tools)
        selected = select_tools(
            tools, task=task, max_tools=max_tools, max_schema_tokens=max_schema_tokens
        )
        lock = scan.to_lock(selected_tools=selected)
        flags = load_response_fixtures(fixtures, max_response_tokens=max_response_tokens)
        lock["response_fixture_flags"] = flags
        passed, violations = check_lock(
            lock,
            max_schema_tokens=max_schema_tokens,
            max_response_tokens=max_response_tokens + 1000000,
            require_tools=list(GOLDEN_TOOLS[:3]),
        )
        return {
            "servers": scan.server_count,
            "tools": scan.tool_count,
            "before_schema_tokens": scan.total_schema_tokens,
            "selected_tools": len(selected),
            "after_schema_tokens": lock["selected_schema_tokens"],
            "oversized_response_fixture": "flagged" if flags else "not_flagged",
            "budget_status": "PASS" if passed else "FAIL",
            "violations": violations,
            "selected_tool_ids": lock["selected_tools"],
        }

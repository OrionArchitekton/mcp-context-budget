from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_context_budget.budget import ScanResult


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def markdown_report(scan: ScanResult, lock: dict[str, Any]) -> str:
    lines = [
        "# MCP Context Budget Report",
        "",
        f"- Servers: {scan.server_count}",
        f"- Tools: {scan.tool_count}",
        f"- Total schema tokens: {scan.total_schema_tokens}",
        f"- Selected schema tokens: {lock.get('selected_schema_tokens')}",
        "",
        "## Server Hot Spots",
        "",
        "| server | schema_tokens |",
        "|---|---:|",
    ]
    for server, tokens in scan.by_server().items():
        lines.append(f"| {server} | {tokens} |")
    lines.extend(["", "## Selected Tools", ""])
    for tool_id in lock.get("selected_tools", []):
        lines.append(f"- `{tool_id}`")
    return "\n".join(lines) + "\n"


def sarif_from_lock(lock: dict[str, Any]) -> dict[str, Any]:
    results = []
    for tool_id, item in sorted((lock.get("tools") or {}).items()):
        if tool_id not in set(lock.get("selected_tools") or []):
            continue
        results.append(
            {
                "ruleId": "mcp-context-budget.selected-tool",
                "level": "note",
                "message": {
                    "text": f"{tool_id} selected with {item.get('schema_tokens')} schema tokens"
                },
                "locations": [],
            }
        )
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "mcp-context-budget",
                        "rules": [
                            {
                                "id": "mcp-context-budget.selected-tool",
                                "shortDescription": {"text": "Selected MCP tool budget entry"},
                            }
                        ],
                    }
                },
                "results": results,
            }
        ],
    }

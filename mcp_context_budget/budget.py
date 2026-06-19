from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_context_budget.models import ToolRecord, fingerprint_tool_ids
from mcp_context_budget.tokens import ESTIMATOR_MODE, estimate_tokens


@dataclass(frozen=True)
class ScanResult:
    tools: list[ToolRecord]

    @property
    def total_schema_tokens(self) -> int:
        return sum(tool.schema_tokens for tool in self.tools)

    @property
    def server_count(self) -> int:
        return len({tool.server for tool in self.tools})

    @property
    def tool_count(self) -> int:
        return len(self.tools)

    def by_server(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for tool in self.tools:
            totals[tool.server] = totals.get(tool.server, 0) + tool.schema_tokens
        return dict(sorted(totals.items(), key=lambda item: (-item[1], item[0])))

    def to_lock(self, *, selected_tools: list[ToolRecord] | None = None) -> dict[str, Any]:
        selected = selected_tools if selected_tools is not None else self.tools
        return {
            "schema_version": 2,
            "estimator": ESTIMATOR_MODE,
            "config_fingerprint": fingerprint_tool_ids(tool.tool_id for tool in self.tools),
            "server_count": self.server_count,
            "tool_count": self.tool_count,
            "total_schema_tokens": self.total_schema_tokens,
            "selected_schema_tokens": sum(tool.schema_tokens for tool in selected),
            "selected_tools": [tool.tool_id for tool in selected],
            "tools": {
                tool.tool_id: {
                    "server": tool.server,
                    "name": tool.name,
                    "schema_hash": tool.schema_hash,
                    "schema_tokens": tool.schema_tokens,
                    "description": tool.description,
                    "tags": list(tool.tags),
                    "profile": tool.profile,
                }
                for tool in self.tools
            },
        }


def scan_records(tools: list[ToolRecord]) -> ScanResult:
    if not tools:
        raise ValueError("no tools found to scan")
    return ScanResult(tools=tools)


def load_response_fixtures(path: Path | None, *, max_response_tokens: int) -> list[dict[str, Any]]:
    if path is None:
        return []
    files = [path] if path.is_file() else sorted(path.glob("*.json"))
    flags: list[dict[str, Any]] = []
    for file in files:
        payload = json.loads(file.read_text(encoding="utf-8"))
        body = payload.get("response", payload)
        token_count = estimate_tokens(json.dumps(body, sort_keys=True))
        if token_count > max_response_tokens:
            flags.append(
                {
                    "file": str(file),
                    "tool": payload.get("tool", file.stem),
                    "response_tokens": token_count,
                    "max_response_tokens": max_response_tokens,
                }
            )
    return flags


def check_lock(
    lock: dict[str, Any],
    *,
    max_schema_tokens: int,
    max_response_tokens: int | None = None,
    require_tools: list[str] | None = None,
) -> tuple[bool, list[str]]:
    violations: list[str] = []
    selected_schema_tokens = int(lock.get("selected_schema_tokens") or 0)
    if selected_schema_tokens > max_schema_tokens:
        violations.append(
            f"selected_schema_tokens {selected_schema_tokens} exceeds cap {max_schema_tokens}"
        )
    if max_response_tokens is not None:
        for flag in lock.get("response_fixture_flags", []):
            if int(flag.get("response_tokens") or 0) > max_response_tokens:
                violations.append(
                    f"response fixture {flag.get('tool')} exceeds cap {max_response_tokens}"
                )
    selected = set(lock.get("selected_tools") or [])
    for tool in require_tools or []:
        if tool not in selected:
            violations.append(f"required tool missing from selection: {tool}")
    return not violations, violations

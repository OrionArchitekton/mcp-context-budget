from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_context_budget.models import ToolRecord

REDACTION = "<redacted>"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at {path}: {exc}") from exc


def redact_env(env: object) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    return {str(key): REDACTION for key in sorted(env)}


def is_enabled(raw: object) -> bool:
    """A tool/server entry is honored unless it explicitly carries `enabled: false`.

    This is the read side of the config-apply contract: once `config-apply` writes
    `enabled: false`, a rescan must DROP that tool so the disable actually takes
    effect on the budget. Non-dict entries return True so downstream parsing raises
    the usual structural error rather than being silently skipped.
    """
    return not (isinstance(raw, dict) and raw.get("enabled") is False)


def _server_items(payload: Any, *, default_server: str = "default") -> list[tuple[str, list[Any]]]:
    if isinstance(payload, list):
        return [(default_server, payload)]
    if not isinstance(payload, dict):
        raise ValueError("tool list must be a JSON object or array")
    if isinstance(payload.get("servers"), list):
        items: list[tuple[str, list[Any]]] = []
        for server in payload["servers"]:
            if not isinstance(server, dict):
                continue
            name = str(server.get("name") or server.get("server") or default_server)
            tools = server.get("tools")
            if isinstance(tools, list):
                items.append((name, tools))
        return items
    tools = payload.get("tools")
    if isinstance(tools, list):
        return [(default_server, tools)]
    raise ValueError("tool list JSON must contain `tools` or `servers[].tools`")


def _tool_from_payload(server: str, raw: Any) -> ToolRecord:
    if not isinstance(raw, dict):
        raise ValueError("each tool entry must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("tool entry missing non-empty `name`")
    input_schema = raw.get("inputSchema") or raw.get("input_schema") or raw.get("schema") or {}
    if not isinstance(input_schema, dict):
        raise ValueError(f"tool {name!r} input schema must be an object")
    tags_raw = raw.get("tags") or raw.get("profiles") or []
    tags = (
        tuple(str(tag) for tag in tags_raw if isinstance(tag, str))
        if isinstance(tags_raw, list)
        else ()
    )
    profile = raw.get("profile")
    tool_server = raw.get("server") if isinstance(raw.get("server"), str) else server
    return ToolRecord(
        server=tool_server,
        name=name,
        description=str(raw.get("description") or ""),
        input_schema=input_schema,
        tags=tags,
        profile=str(profile or "default"),
    )


def load_tool_list(path: Path, *, default_server: str = "default") -> list[ToolRecord]:
    payload = read_json(path)
    records: list[ToolRecord] = []
    for server, tools in _server_items(payload, default_server=default_server):
        records.extend(_tool_from_payload(server, raw) for raw in tools if is_enabled(raw))
    return records


def load_mcp_config(
    path: Path, *, allow_start: bool = False
) -> tuple[list[ToolRecord], dict[str, Any]]:
    payload = read_json(path)
    servers = (
        payload.get("mcpServers") or payload.get("servers") if isinstance(payload, dict) else None
    )
    if not isinstance(servers, dict):
        raise ValueError("MCP config must contain an `mcpServers` object")
    records: list[ToolRecord] = []
    manifest: dict[str, Any] = {"servers": {}}
    for name, raw in sorted(servers.items()):
        if not isinstance(raw, dict):
            continue
        manifest["servers"][name] = {
            "command": raw.get("command"),
            "args": raw.get("args") if isinstance(raw.get("args"), list) else [],
            "env": redact_env(raw.get("env")),
            "allow_start": allow_start,
        }
        if raw.get("enabled") is False:
            manifest["servers"][name]["disabled"] = True
            continue
        inline_tools = raw.get("tools")
        if isinstance(inline_tools, list):
            records.extend(
                _tool_from_payload(name, tool) for tool in inline_tools if is_enabled(tool)
            )
        tool_path = (
            raw.get("toolsListPath") or raw.get("tools_list_path") or raw.get("toolListPath")
        )
        if isinstance(tool_path, str) and tool_path:
            resolved = Path(tool_path)
            if not resolved.is_absolute():
                resolved = path.parent / resolved
            records.extend(load_tool_list(resolved, default_server=name))
        if allow_start and not inline_tools and not tool_path:
            command = raw.get("command")
            args = raw.get("args") if isinstance(raw.get("args"), list) else []
            rendered = " ".join([str(command), *(str(arg) for arg in args)]).strip()
            raise NotImplementedError(
                "safe live stdio startup is deferred to v0.2; would start: "
                f"{rendered} with env={manifest['servers'][name]['env']}"
            )
    return records, manifest


def load_records(
    *, tool_list: Path | None = None, config: Path | None = None, allow_start: bool = False
) -> tuple[list[ToolRecord], dict[str, Any]]:
    if tool_list is None and config is None:
        raise ValueError("provide --tool-list or --config")
    if tool_list is not None:
        return load_tool_list(tool_list), {"source": str(tool_list)}
    if config is None:
        raise ValueError("provide --tool-list or --config")
    return load_mcp_config(config, allow_start=allow_start)

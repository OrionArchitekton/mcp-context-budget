from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_context_budget.live_stdio import introspect_server_tools
from mcp_context_budget.loaders import (
    REDACTION,
    _server_items,
    load_mcp_config,
    read_json,
    redact_env,
    stdio_framing_for_server,
)
from mcp_context_budget.models import fingerprint_tool_ids

_TOOLS_LIST_KEYS = ("toolsListPath", "tools_list_path", "toolListPath")


def _exclusive_backup_path(backup_root: Path, config_name: str) -> Path:
    """Return a backup path that does not yet exist, never clobbering a prior rollback copy."""
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S%f")
    candidate = backup_root / f"{config_name}.{stamp}.bak"
    counter = 1
    while candidate.exists():
        candidate = backup_root / f"{config_name}.{stamp}.{counter}.bak"
        counter += 1
    return candidate


def _mcp_servers(payload: Any) -> dict[str, Any]:
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    if not isinstance(servers, dict):
        raise ValueError("MCP config must contain an `mcpServers` object")
    return servers


def _selected_tools(lock_payload: dict[str, Any]) -> set[str]:
    selected = lock_payload.get("selected_tools")
    if not isinstance(selected, list):
        raise ValueError("lock must contain a `selected_tools` list")
    if not all(isinstance(tool, str) for tool in selected):
        raise ValueError("lock `selected_tools` must contain only strings")
    return set(selected)


def _tools_list_path(config_path: Path, raw: dict[str, Any]) -> Path | None:
    for key in _TOOLS_LIST_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value:
            resolved = Path(value)
            return resolved if resolved.is_absolute() else config_path.parent / resolved
    return None


def _relative_or_absolute(path: Path, *, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _safe_tools_filename(server: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", server).strip(".-")
    # Distinct server names can sanitize to the same cleaned string; append a
    # short hash of the original name so materialized sidecars never collide.
    digest = hashlib.sha256(server.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned or 'server'}-{digest}.tools.json"


def _tools_payload_from_live(raw_tools: list[dict[str, Any]]) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    for raw in raw_tools:
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            continue
        tool: dict[str, Any] = {
            "name": name,
            "description": str(raw.get("description") or ""),
            "inputSchema": raw.get("inputSchema")
            if isinstance(raw.get("inputSchema"), dict)
            else raw.get("input_schema")
            if isinstance(raw.get("input_schema"), dict)
            else raw.get("schema")
            if isinstance(raw.get("schema"), dict)
            else {},
        }
        if isinstance(raw.get("tags"), list):
            tool["tags"] = [str(tag) for tag in raw["tags"] if isinstance(tag, str)]
        if isinstance(raw.get("profile"), str):
            tool["profile"] = raw["profile"]
        tools.append(tool)
    if not tools:
        raise ValueError("live MCP server returned no usable tools")
    return {"tools": tools}


def _action_counts_by_server(actions: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        server = action.get("server") or "unknown"
        counts[server] = counts.get(server, 0) + 1
    return dict(sorted(counts.items()))


def _disable_unselected(
    tools: list[Any], server: str, selected_tools: set[str], target: str
) -> list[dict[str, str]]:
    """Set `enabled: false` on every unselected tool entry, in place. Returns actions."""
    actions: list[dict[str, str]] = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            continue
        tool_server = tool.get("server") if isinstance(tool.get("server"), str) else server
        tool_id = f"{tool_server}/{tool['name']}"
        if tool_id not in selected_tools and tool.get("enabled", True):
            tool["enabled"] = False
            actions.append(
                {
                    "action": "disable_tool",
                    "server": tool_server,
                    "tool": tool["name"],
                    "target": target,
                }
            )
    return actions


def build_config_patch(
    payload: dict[str, Any],
    *,
    selected_tools: set[str],
    mode: str,
    config_path: Path,
    allow_start: bool = False,
    start_timeout_seconds: float = 5.0,
    max_stdio_bytes: int = 65536,
    stdio_framing: str = "auto",
    materialize_tools_list: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]], list[dict[str, str]]]:
    """Plan the disable-unselected patch across inline tools AND toolsListPath files.

    Returns (updated_main_payload, actions, external_patches, not_patchable):
      - external_patches: [{"path", "before_text", "payload", "actions": int}] to write
      - not_patchable:    [{"server", "reason"}] servers whose tools could not be enforced
    """
    if mode != "disable-unselected":
        raise ValueError("only disable-unselected mode is supported")
    _mcp_servers(payload)
    updated = json.loads(json.dumps(payload))
    actions: list[dict[str, str]] = []
    external_patches: list[dict[str, Any]] = []
    not_patchable: list[dict[str, str]] = []

    for server, raw in sorted(updated["mcpServers"].items()):
        if not isinstance(raw, dict):
            continue
        if raw.get("enabled") is False:
            continue  # server already fully disabled; nothing to enforce

        inline = raw.get("tools")
        has_inline = isinstance(inline, list)
        if has_inline:
            actions.extend(_disable_unselected(inline, str(server), selected_tools, "inline"))

        tools_path = _tools_list_path(config_path, raw)
        if tools_path is not None:
            if not tools_path.exists():
                not_patchable.append(
                    {"server": str(server), "reason": f"toolsListPath not found: {tools_path}"}
                )
            else:
                before_text = tools_path.read_text(encoding="utf-8")
                ext_payload = json.loads(before_text)
                try:
                    pairs = _server_items(ext_payload, default_server=str(server))
                except ValueError as exc:
                    not_patchable.append(
                        {"server": str(server), "reason": f"toolsListPath unpatchable shape: {exc}"}
                    )
                else:
                    ext_actions: list[dict[str, str]] = []
                    for list_server, tool_entries in pairs:
                        ext_actions.extend(
                            _disable_unselected(
                                tool_entries, list_server, selected_tools, str(tools_path)
                            )
                        )
                    actions.extend(ext_actions)
                    if ext_actions:
                        external_patches.append(
                            {
                                "path": tools_path,
                                "before_text": before_text,
                                "payload": ext_payload,
                                "actions": len(ext_actions),
                                "materialized": False,
                            }
                        )

        if not has_inline and tools_path is None:
            if allow_start and materialize_tools_list is not None:
                materialize_root = materialize_tools_list
                if not materialize_root.is_absolute():
                    materialize_root = config_path.parent / materialize_root
                tools_path = materialize_root / _safe_tools_filename(str(server))
                try:
                    live = introspect_server_tools(
                        server=str(server),
                        command=raw.get("command"),
                        args=raw.get("args") if isinstance(raw.get("args"), list) else [],
                        env=raw.get("env"),
                        start_timeout_seconds=start_timeout_seconds,
                        max_stdio_bytes=max_stdio_bytes,
                        stdio_framing=stdio_framing_for_server(
                            raw, default=stdio_framing
                        ),
                    )
                except ValueError as exc:
                    # Keep the failure in the per-server report rather than aborting
                    # the whole apply: one unreachable server must not sink the run.
                    not_patchable.append(
                        {
                            "server": str(server),
                            "reason": f"live introspection failed: {exc}",
                        }
                    )
                    continue
                ext_payload = _tools_payload_from_live(live.tools)
                ext_actions = _disable_unselected(
                    ext_payload["tools"], str(server), selected_tools, str(tools_path)
                )
                raw["toolsListPath"] = _relative_or_absolute(tools_path, base=config_path.parent)
                actions.append(
                    {
                        "action": "materialize_tools_list",
                        "server": str(server),
                        "tool": "*",
                        "target": str(tools_path),
                    }
                )
                actions.extend(ext_actions)
                external_patches.append(
                    {
                        "path": tools_path,
                        # Preserve any existing sidecar so --write backs it up
                        # before overwriting (never clobber without a backup).
                        "before_text": (
                            tools_path.read_text(encoding="utf-8") if tools_path.exists() else None
                        ),
                        "payload": ext_payload,
                        "actions": len(ext_actions),
                        "materialized": True,
                    }
                )
            else:
                reason = "enforcement requires live startup (--allow-start), deferred"
                if allow_start:
                    reason = (
                        "live tools were listed but config enforcement requires "
                        "--materialize-tools-list"
                    )
                not_patchable.append(
                    {
                        "server": str(server),
                        "reason": (
                            "tools are command-discovered (no inline tools or toolsListPath); "
                            f"{reason}"
                        ),
                    }
                )

    return updated, actions, external_patches, not_patchable


def apply_config_selection(
    *,
    config_path: Path,
    lock_path: Path,
    mode: str = "disable-unselected",
    write: bool = False,
    backup_dir: Path | None = None,
    patch_out: Path | None = None,
    allow_fingerprint_mismatch: bool = False,
    allow_start: bool = False,
    start_timeout_seconds: float = 5.0,
    max_stdio_bytes: int = 65536,
    stdio_framing: str = "auto",
    materialize_tools_list: Path | None = None,
) -> dict[str, Any]:
    before_text = config_path.read_text(encoding="utf-8")
    payload = json.loads(before_text)
    lock_payload = read_json(lock_path)
    if not isinstance(lock_payload, dict):
        raise ValueError("lock must be a JSON object")
    selected = _selected_tools(lock_payload)

    # Lock <-> config binding: refuse a lock generated for a different tool universe
    # (a foreign/stale lock would otherwise disable every tool and report success).
    try:
        target_records, _ = load_mcp_config(
            config_path,
            allow_start=allow_start,
            start_timeout_seconds=start_timeout_seconds,
            max_stdio_bytes=max_stdio_bytes,
            stdio_framing=stdio_framing,
        )
        target_load_error = None
    except ValueError as exc:
        target_records = []
        target_load_error = str(exc)
    if target_load_error is not None and not allow_fingerprint_mismatch:
        # Fail closed: if the target config cannot be loaded we cannot verify the
        # lock<->config binding, so refuse rather than patch a config we could not
        # parse (proceeding would treat it as having no tools and disable blindly).
        raise ValueError(
            f"cannot verify the lock against this config: {target_load_error}; "
            "fix the config or pass --allow-fingerprint-mismatch to override"
        )
    target_ids = {record.tool_id for record in target_records}
    target_fingerprint = fingerprint_tool_ids(target_ids)
    lock_fingerprint = lock_payload.get("config_fingerprint")
    fingerprint_match: bool | None
    if isinstance(lock_fingerprint, str) and lock_fingerprint:
        fingerprint_match = lock_fingerprint == target_fingerprint
        if not fingerprint_match and not allow_fingerprint_mismatch:
            raise ValueError(
                "lock does not match this config (config_fingerprint mismatch): the lock was "
                "generated for a different tool universe; re-run `select`/`scan` against this "
                "config, or pass --allow-fingerprint-mismatch to override"
            )
    else:
        fingerprint_match = None  # legacy lock with no fingerprint; report null
    # Defense-in-depth (covers fingerprint-less locks too): a non-empty selection that
    # shares NO tool with this config is foreign -- refuse rather than disable everything.
    if (
        selected
        and target_ids
        and selected.isdisjoint(target_ids)
        and not allow_fingerprint_mismatch
    ):
        raise ValueError(
            "lock does not match this config: none of its selected_tools exist here; "
            "re-run `select`/`scan` against this config, or pass --allow-fingerprint-mismatch"
        )

    updated, actions, external_patches, not_patchable = build_config_patch(
        payload,
        selected_tools=selected,
        mode=mode,
        config_path=config_path,
        allow_start=allow_start,
        start_timeout_seconds=start_timeout_seconds,
        max_stdio_bytes=max_stdio_bytes,
        stdio_framing=stdio_framing,
        materialize_tools_list=materialize_tools_list,
    )
    servers = _mcp_servers(payload)
    status = "PARTIAL" if not_patchable else "PASS"
    report: dict[str, Any] = {
        "config": str(config_path),
        "mode": mode,
        "dry_run": not write,
        "status": status,
        "config_fingerprint": lock_fingerprint if isinstance(lock_fingerprint, str) else None,
        "target_fingerprint": target_fingerprint,
        "target_load_error": target_load_error,
        "fingerprint_match": fingerprint_match,
        "actions": actions,
        "action_counts_by_server": _action_counts_by_server(actions),
        "not_patchable": not_patchable,
        "external_targets": [str(p["path"]) for p in external_patches],
        "servers": {
            str(name): {"env": redact_env(raw.get("env")) if isinstance(raw, dict) else {}}
            for name, raw in sorted(servers.items())
        },
    }

    if write and actions:
        backup_root = backup_dir or config_path.parent
        backup_root.mkdir(parents=True, exist_ok=True)
        written_backups: list[str] = []
        # main config
        main_backup = _exclusive_backup_path(backup_root, config_path.name)
        with main_backup.open("xb") as handle:
            handle.write(before_text.encode("utf-8"))
        written_backups.append(str(main_backup))
        config_path.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        # external toolsListPath files
        for patch in external_patches:
            ext_path: Path = patch["path"]
            ext_path.parent.mkdir(parents=True, exist_ok=True)
            if patch["before_text"] is not None:
                ext_backup = _exclusive_backup_path(backup_root, ext_path.name)
                with ext_backup.open("xb") as handle:
                    handle.write(patch["before_text"].encode("utf-8"))
                written_backups.append(str(ext_backup))
            ext_path.write_text(
                json.dumps(patch["payload"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        report["backup_path"] = written_backups[0]
        report["backup_paths"] = written_backups

    if patch_out is not None:
        patch_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if REDACTION not in json.dumps(report) and any(
        isinstance(raw, dict) and raw.get("env") for raw in servers.values()
    ):
        raise ValueError("config report did not include redacted env metadata")
    return report


def run_config_multiserver_demo() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mcp-context-budget-multiserver-") as tmp:
        root = Path(tmp)
        config = root / "mcp.json"
        ext_tools = root / "catalog.tools.json"
        lock = root / "lock.json"
        ext_tools.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "github",
                            "tools": [
                                {"name": "get_issue", "description": "Get issue"},
                                {"name": "delete_repo", "description": "Delete repo"},
                            ],
                        },
                        {
                            "name": "linear",
                            "tools": [
                                {"name": "create_issue", "description": "Create issue"},
                                {"name": "bulk_delete", "description": "Bulk delete"},
                            ],
                        },
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "catalog": {
                            "command": "catalog-mcp",
                            "toolsListPath": "catalog.tools.json",
                        }
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        records, _ = load_mcp_config(config)
        lock.write_text(
            json.dumps(
                {
                    "selected_tools": ["github/get_issue", "linear/create_issue"],
                    "config_fingerprint": fingerprint_tool_ids(r.tool_id for r in records),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        report = apply_config_selection(
            config_path=config, lock_path=lock, write=True, backup_dir=root / "backups"
        )
        remaining = {r.tool_id for r in load_mcp_config(config)[0]}
    return {
        "config_multiserver_external_patched": len(report["external_targets"]),
        "config_multiserver_actions": len(report["actions"]),
        "config_multiserver_action_servers": sorted(report["action_counts_by_server"]),
        "config_multiserver_rescan_enforced": (
            "github/get_issue" in remaining
            and "linear/create_issue" in remaining
            and "github/delete_repo" not in remaining
            and "linear/bulk_delete" not in remaining
        ),
        "config_multiserver_status": report["status"],
    }


def run_config_demo() -> dict[str, Any]:
    """Exercise the full v0.2 config contract: inline + toolsListPath patching, a
    command-discovered (not-patchable) server, and lock<->config fingerprint binding."""
    with tempfile.TemporaryDirectory(prefix="mcp-context-budget-config-") as tmp:
        root = Path(tmp)
        config = root / "mcp.json"
        ext_tools = root / "linear.tools.json"
        lock = root / "lock.json"
        ext_tools.write_text(
            json.dumps(
                {
                    "tools": [
                        {"name": "create_issue", "description": "Create a Linear issue"},
                        {"name": "bulk_delete", "description": "Delete many issues"},
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "github": {
                            "command": "github-mcp",
                            "env": {"GITHUB_TOKEN": "demo-secret"},
                            "tools": [
                                {"name": "get_issue", "description": "Get issue"},
                                {"name": "delete_repo", "description": "Delete repository"},
                            ],
                        },
                        "linear": {"command": "linear-mcp", "toolsListPath": "linear.tools.json"},
                        "shell": {"command": "shell-mcp"},  # command-discovered → not patchable
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # A lock bound to THIS config (fingerprint must match), selecting one tool.
        records, _ = load_mcp_config(config, allow_start=False)
        lock.write_text(
            json.dumps(
                {
                    "selected_tools": ["github/get_issue", "linear/create_issue"],
                    "config_fingerprint": fingerprint_tool_ids(r.tool_id for r in records),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before = config.read_text(encoding="utf-8")
        dry_report = apply_config_selection(
            config_path=config, lock_path=lock, write=False, patch_out=root / "patch.json"
        )
        dry_run_unchanged = config.read_text(encoding="utf-8") == before
        write_report = apply_config_selection(
            config_path=config, lock_path=lock, write=True, backup_dir=root / "backups"
        )
        # Prove enforcement actually takes effect: a rescan now drops the disabled tools.
        rescan_records, _ = load_mcp_config(config, allow_start=False)
        remaining = {r.tool_id for r in rescan_records}

    return {
        "config_patch_actions": len(dry_report["actions"]),
        "config_dry_run_unchanged": dry_run_unchanged,
        "config_write_backup_created": bool(write_report.get("backup_paths")),
        "config_external_patched": len(write_report["external_targets"]),
        "config_not_patchable": len(write_report["not_patchable"]),
        "config_fingerprint_match": bool(write_report["fingerprint_match"]),
        "config_remaining_after_disable": sorted(remaining),
        "config_apply_status": write_report["status"],
    }

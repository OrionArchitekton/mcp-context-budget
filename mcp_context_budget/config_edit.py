from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_context_budget.loaders import REDACTION, read_json, redact_env


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


def _selected_tools(lock_path: Path) -> set[str]:
    payload = read_json(lock_path)
    selected = payload.get("selected_tools") if isinstance(payload, dict) else None
    if not isinstance(selected, list):
        raise ValueError("lock must contain a `selected_tools` list")
    return {str(tool) for tool in selected}


def build_config_patch(
    payload: dict[str, Any], *, selected_tools: set[str], mode: str
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if mode != "disable-unselected":
        raise ValueError("only disable-unselected mode is supported")
    _mcp_servers(payload)
    updated = json.loads(json.dumps(payload))
    actions: list[dict[str, str]] = []
    for server, raw in sorted(updated["mcpServers"].items()):
        if not isinstance(raw, dict) or not isinstance(raw.get("tools"), list):
            continue
        for tool in raw["tools"]:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            tool_id = f"{server}/{tool['name']}"
            if tool_id not in selected_tools and tool.get("enabled", True):
                tool["enabled"] = False
                actions.append(
                    {"action": "disable_tool", "server": str(server), "tool": tool["name"]}
                )
    return updated, actions


def apply_config_selection(
    *,
    config_path: Path,
    lock_path: Path,
    mode: str = "disable-unselected",
    write: bool = False,
    backup_dir: Path | None = None,
    patch_out: Path | None = None,
) -> dict[str, Any]:
    before_text = config_path.read_text(encoding="utf-8")
    payload = json.loads(before_text)
    selected = _selected_tools(lock_path)
    updated, actions = build_config_patch(payload, selected_tools=selected, mode=mode)
    servers = _mcp_servers(payload)
    report: dict[str, Any] = {
        "config": str(config_path),
        "mode": mode,
        "dry_run": not write,
        "actions": actions,
        "servers": {
            str(name): {"env": redact_env(raw.get("env")) if isinstance(raw, dict) else {}}
            for name, raw in sorted(servers.items())
        },
    }
    backup_path: Path | None = None
    if write and actions:
        backup_root = backup_dir or config_path.parent
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = _exclusive_backup_path(backup_root, config_path.name)
        with backup_path.open("xb") as handle:
            handle.write(before_text.encode("utf-8"))
        config_path.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        report["backup_path"] = str(backup_path)
    if patch_out is not None:
        patch_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if REDACTION not in json.dumps(report) and any(
        isinstance(raw, dict) and raw.get("env") for raw in servers.values()
    ):
        raise ValueError("config report did not include redacted env metadata")
    return report


def run_config_demo() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mcp-context-budget-config-") as tmp:
        root = Path(tmp)
        config = root / "mcp.json"
        lock = root / "lock.json"
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
                        }
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        lock.write_text(json.dumps({"selected_tools": ["github/get_issue"]}) + "\n")
        before = config.read_text(encoding="utf-8")
        dry_report = apply_config_selection(
            config_path=config,
            lock_path=lock,
            mode="disable-unselected",
            write=False,
            patch_out=root / "patch.json",
        )
        dry_run_unchanged = config.read_text(encoding="utf-8") == before
        write_report = apply_config_selection(
            config_path=config,
            lock_path=lock,
            mode="disable-unselected",
            write=True,
            backup_dir=root / "backups",
        )
    return {
        "config_patch_actions": len(dry_report["actions"]),
        "config_dry_run_unchanged": dry_run_unchanged,
        "config_write_backup_created": bool(write_report.get("backup_path")),
        "config_apply_status": "PASS",
    }

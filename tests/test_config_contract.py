from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_context_budget.config_edit import apply_config_selection, run_config_demo
from mcp_context_budget.loaders import load_mcp_config
from mcp_context_budget.models import fingerprint_tool_ids


def _write(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj) + "\n", encoding="utf-8")


def _bound_lock(config: Path, selected: list[str]) -> dict[str, object]:
    records, _ = load_mcp_config(config)
    return {
        "selected_tools": selected,
        "config_fingerprint": fingerprint_tool_ids(r.tool_id for r in records),
    }


def test_loader_honors_enabled_false_tool(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    _write(
        cfg,
        {
            "mcpServers": {
                "gh": {"command": "x", "tools": [{"name": "a"}, {"name": "b", "enabled": False}]}
            }
        },
    )
    ids = {r.tool_id for r in load_mcp_config(cfg)[0]}
    assert "gh/a" in ids
    assert "gh/b" not in ids  # explicitly-disabled tool drops out of the budget


def test_loader_honors_server_enabled_false(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    _write(
        cfg,
        {
            "mcpServers": {
                "gh": {"command": "x", "tools": [{"name": "a"}]},
                "off": {"command": "y", "enabled": False, "tools": [{"name": "c"}]},
            }
        },
    )
    ids = {r.tool_id for r in load_mcp_config(cfg)[0]}
    assert ids == {"gh/a"}


def test_apply_refuses_foreign_lock_fingerprint(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    _write(cfg, {"mcpServers": {"gh": {"command": "x", "tools": [{"name": "a"}, {"name": "b"}]}}})
    lock = tmp_path / "lock.json"
    _write(lock, {"selected_tools": ["other/zzz"], "config_fingerprint": "deadbeef"})
    with pytest.raises(ValueError, match="mismatch"):
        apply_config_selection(config_path=cfg, lock_path=lock, write=False)
    # explicit override proceeds but is flagged as a mismatch (unsafe path)
    report = apply_config_selection(
        config_path=cfg, lock_path=lock, write=False, allow_fingerprint_mismatch=True
    )
    assert report["fingerprint_match"] is False


def test_apply_patches_inline_and_toolslistpath(tmp_path: Path) -> None:
    ext = tmp_path / "lin.tools.json"
    _write(ext, {"tools": [{"name": "create"}, {"name": "nuke"}]})
    cfg = tmp_path / "mcp.json"
    _write(
        cfg,
        {
            "mcpServers": {
                "gh": {"command": "x", "tools": [{"name": "get"}, {"name": "del"}]},
                "lin": {"command": "y", "toolsListPath": "lin.tools.json"},
            }
        },
    )
    lock = tmp_path / "lock.json"
    _write(lock, _bound_lock(cfg, ["gh/get", "lin/create"]))

    report = apply_config_selection(
        config_path=cfg, lock_path=lock, write=True, backup_dir=tmp_path / "bak"
    )
    assert report["status"] == "PASS"
    assert report["fingerprint_match"] is True
    assert len(report["backup_paths"]) == 2  # main config + external tools file

    # enforcement actually takes effect on rescan
    ids = {r.tool_id for r in load_mcp_config(cfg)[0]}
    assert ids == {"gh/get", "lin/create"}

    # the external toolsListPath file was patched ON DISK (not silently skipped)
    by_name = {t["name"]: t for t in json.loads(ext.read_text())["tools"]}
    assert by_name["nuke"]["enabled"] is False
    assert by_name["create"].get("enabled", True) is True


def test_command_discovered_server_is_partial_not_false_pass(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    _write(
        cfg,
        {
            "mcpServers": {
                "gh": {"command": "x", "tools": [{"name": "get"}, {"name": "del"}]},
                "shell": {"command": "shell-mcp"},
            }
        },
    )  # command-discovered: no inline tools/toolsListPath
    lock = tmp_path / "lock.json"
    _write(lock, _bound_lock(cfg, ["gh/get"]))
    report = apply_config_selection(config_path=cfg, lock_path=lock, write=False)
    assert report["status"] == "PARTIAL"  # NOT a false PASS
    assert any(item["server"] == "shell" for item in report["not_patchable"])


def test_config_demo_proves_full_contract() -> None:
    result = run_config_demo()
    assert result["config_fingerprint_match"] is True
    assert result["config_external_patched"] >= 1
    assert result["config_not_patchable"] >= 1
    assert result["config_apply_status"] == "PARTIAL"
    remaining = set(result["config_remaining_after_disable"])
    assert "github/delete_repo" not in remaining
    assert "linear/bulk_delete" not in remaining
    assert {"github/get_issue", "linear/create_issue"} <= remaining

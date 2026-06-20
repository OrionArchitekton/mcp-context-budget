from __future__ import annotations

import json
import sys
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


def test_allow_start_materializes_command_discovered_server(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    _write(
        cfg,
        {
            "mcpServers": {
                "fixture": {
                    "command": sys.executable,
                    "args": ["-m", "mcp_context_budget", "_fixture-mcp-server"],
                }
            }
        },
    )
    records, _ = load_mcp_config(cfg, allow_start=True, start_timeout_seconds=2)
    lock = tmp_path / "lock.json"
    _write(
        lock,
        {
            "selected_tools": ["fixture/safe_read"],
            "config_fingerprint": fingerprint_tool_ids(r.tool_id for r in records),
        },
    )

    report = apply_config_selection(
        config_path=cfg,
        lock_path=lock,
        write=True,
        backup_dir=tmp_path / "backups",
        allow_start=True,
        start_timeout_seconds=2,
        materialize_tools_list=tmp_path / "materialized",
    )

    assert report["status"] == "PASS"
    assert report["not_patchable"] == []
    assert report["external_targets"]
    payload = json.loads(cfg.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["fixture"]["toolsListPath"] == "materialized/fixture.tools.json"
    remaining = {r.tool_id for r in load_mcp_config(cfg, allow_start=False)[0]}
    assert remaining == {"fixture/safe_read"}


def test_multiserver_toolslistpath_catalog_is_patched(tmp_path: Path) -> None:
    ext = tmp_path / "catalog.tools.json"
    _write(
        ext,
        {
            "servers": [
                {"name": "github", "tools": [{"name": "get"}, {"name": "delete"}]},
                {"name": "linear", "tools": [{"name": "create"}, {"name": "bulk_delete"}]},
            ]
        },
    )
    cfg = tmp_path / "mcp.json"
    _write(cfg, {"mcpServers": {"catalog": {"command": "x", "toolsListPath": ext.name}}})
    lock = tmp_path / "lock.json"
    _write(lock, _bound_lock(cfg, ["github/get", "linear/create"]))

    report = apply_config_selection(
        config_path=cfg, lock_path=lock, write=True, backup_dir=tmp_path / "backups"
    )

    assert report["status"] == "PASS"
    assert report["action_counts_by_server"] == {"github": 1, "linear": 1}
    remaining = {r.tool_id for r in load_mcp_config(cfg)[0]}
    assert remaining == {"github/get", "linear/create"}


def test_multiserver_toolslistpath_bad_shape_is_partial(tmp_path: Path) -> None:
    ext = tmp_path / "catalog.tools.json"
    _write(ext, {"servers": [{"name": "github", "not_tools": []}]})
    cfg = tmp_path / "mcp.json"
    _write(cfg, {"mcpServers": {"catalog": {"command": "x", "toolsListPath": ext.name}}})
    lock = tmp_path / "lock.json"
    _write(lock, {"selected_tools": ["github/get"]})

    report = apply_config_selection(
        config_path=cfg,
        lock_path=lock,
        write=False,
        allow_fingerprint_mismatch=True,
    )

    assert report["status"] == "PARTIAL"
    assert "unpatchable shape" in report["not_patchable"][0]["reason"]


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


def test_apply_rejects_non_string_selected_tools(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    _write(cfg, {"mcpServers": {"gh": {"command": "x", "tools": [{"name": "a"}]}}})
    lock = tmp_path / "lock.json"
    _write(lock, {"selected_tools": ["gh/a", 7, {"name": "b"}]})  # non-string entries
    with pytest.raises(ValueError, match="only strings"):
        apply_config_selection(config_path=cfg, lock_path=lock, write=False)


def test_embedding_vector_rejects_non_finite_values() -> None:
    from mcp_context_budget.semantic import _as_vector

    assert _as_vector([0.1, 0.2], label="ok") == [0.1, 0.2]
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            _as_vector([0.1, bad], label="bad")

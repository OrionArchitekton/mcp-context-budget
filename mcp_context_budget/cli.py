from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mcp_context_budget.budget import check_lock, load_response_fixtures, scan_records
from mcp_context_budget.demo import run_demo
from mcp_context_budget.loaders import load_records
from mcp_context_budget.reporting import markdown_report, sarif_from_lock, write_json
from mcp_context_budget.selector import select_tools


def _add_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path)
    parser.add_argument("--tool-list", type=Path)
    parser.add_argument("--allow-start", action="store_true")


def _records_from_args(args: argparse.Namespace):
    records, manifest = load_records(
        tool_list=args.tool_list, config=args.config, allow_start=args.allow_start
    )
    return records, manifest


def cmd_scan(args: argparse.Namespace) -> int:
    records, manifest = _records_from_args(args)
    scan = scan_records(records)
    lock = scan.to_lock()
    lock["source_manifest"] = manifest
    if args.fixtures:
        lock["response_fixture_flags"] = load_response_fixtures(
            args.fixtures, max_response_tokens=args.max_response_tokens
        )
    if args.out:
        args.out.write_text(markdown_report(scan, lock), encoding="utf-8")
    if args.json_out:
        write_json(args.json_out, lock)
    if args.lock_out:
        write_json(args.lock_out, lock)
    print(f"SCAN_SERVERS={scan.server_count}")
    print(f"SCAN_TOOLS={scan.tool_count}")
    print(f"SCHEMA_TOKENS={scan.total_schema_tokens}")
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    records, manifest = _records_from_args(args)
    scan = scan_records(records)
    selected = select_tools(
        records,
        task=args.task,
        max_tools=args.max_tools,
        max_schema_tokens=args.max_schema_tokens,
    )
    lock = scan.to_lock(selected_tools=selected)
    lock["source_manifest"] = manifest
    if args.out_lock:
        write_json(args.out_lock, lock)
    if args.json_out:
        write_json(args.json_out, lock)
    print(f"SELECTED_TOOLS={len(selected)}")
    print(f"AFTER_SCHEMA_TOKENS={lock['selected_schema_tokens']}")
    for tool_id in lock["selected_tools"]:
        print(f"SELECTED_TOOL={tool_id}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    passed, violations = check_lock(
        lock,
        max_schema_tokens=args.max_schema_tokens,
        max_response_tokens=args.max_response_tokens,
        require_tools=args.require_tool,
    )
    print(f"SELECTED_SCHEMA_TOKENS={lock.get('selected_schema_tokens')}")
    print(f"BUDGET_STATUS={'PASS' if passed else 'FAIL'}")
    for violation in violations:
        print(f"VIOLATION={violation}")
    return 0 if passed else 1


def cmd_export(args: argparse.Namespace) -> int:
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    if args.format == "json":
        write_json(args.out, lock)
    elif args.format == "sarif":
        write_json(args.out, sarif_from_lock(lock))
    else:
        tools = lock.get("tools") or {}
        lines = ["# MCP Context Budget Export", ""]
        lines.append(f"- Selected schema tokens: {lock.get('selected_schema_tokens')}")
        lines.append("")
        for tool_id in lock.get("selected_tools") or []:
            lines.append(f"- `{tool_id}`: {tools.get(tool_id, {}).get('schema_tokens')} tokens")
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"EXPORT_WRITTEN={args.out}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    result = run_demo(
        task=args.task,
        max_tools=args.max_tools,
        max_schema_tokens=args.max_schema_tokens,
        max_response_tokens=args.max_response_tokens,
    )
    print(f"DEMO_CATALOG_SERVERS={result['servers']}")
    print(f"DEMO_CATALOG_TOOLS={result['tools']}")
    print(f"BEFORE_SCHEMA_TOKENS={result['before_schema_tokens']}")
    print(f"SELECTED_TOOLS={result['selected_tools']}")
    print(f"AFTER_SCHEMA_TOKENS={result['after_schema_tokens']}")
    print(f"OVERSIZED_RESPONSE_FIXTURE={result['oversized_response_fixture']}")
    print(f"BUDGET_STATUS={result['budget_status']}")
    for tool_id in result["selected_tool_ids"]:
        print(f"SELECTED_TOOL={tool_id}")
    return 0 if result["budget_status"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-context-budget")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan")
    _add_input_args(scan)
    scan.add_argument("--out", type=Path)
    scan.add_argument("--json-out", type=Path)
    scan.add_argument("--lock-out", type=Path)
    scan.add_argument("--fixtures", type=Path)
    scan.add_argument("--max-response-tokens", type=int, default=4000)
    scan.set_defaults(func=cmd_scan)

    select = sub.add_parser("select")
    _add_input_args(select)
    select.add_argument("--task", required=True)
    select.add_argument("--max-tools", type=int, default=8)
    select.add_argument("--max-schema-tokens", type=int, default=6000)
    select.add_argument("--out-lock", type=Path)
    select.add_argument("--json-out", type=Path)
    select.set_defaults(func=cmd_select)

    check = sub.add_parser("check")
    check.add_argument("--lock", type=Path, required=True)
    check.add_argument("--max-schema-tokens", type=int, required=True)
    check.add_argument("--max-response-tokens", type=int)
    check.add_argument("--require-tool", action="append", default=[])
    check.set_defaults(func=cmd_check)

    export = sub.add_parser("export")
    export.add_argument("--lock", type=Path, required=True)
    export.add_argument("--format", choices=("markdown", "json", "sarif"), default="markdown")
    export.add_argument("--out", type=Path, required=True)
    export.set_defaults(func=cmd_export)

    demo = sub.add_parser("demo")
    demo.add_argument("--task", required=True)
    demo.add_argument("--max-tools", type=int, default=8)
    demo.add_argument("--max-schema-tokens", type=int, default=6000)
    demo.add_argument("--max-response-tokens", type=int, default=4000)
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, NotImplementedError) as exc:
        print(f"mcp-context-budget: {exc}", file=sys.stderr)
        return 2

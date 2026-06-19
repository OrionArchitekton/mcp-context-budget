from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from mcp_context_budget.tokens import estimate_tokens


def _fixture_files(path: Path) -> list[Path]:
    return [path] if path.is_file() else sorted(path.glob("*.json"))


def response_token_count(response: Any) -> int:
    return estimate_tokens(json.dumps(response, sort_keys=True))


def _source_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("summary", "body", "text", "content", "description"):
            value = response.get(key)
            if isinstance(value, str) and value:
                return value
    return json.dumps(response, sort_keys=True)


def _initial_extract(response: Any, keep_fields: list[str]) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"summary": _source_text(response)}
    compact: dict[str, Any] = {}
    for field in keep_fields:
        if field == "summary":
            continue
        if field in response:
            compact[field] = response[field]
    if "summary" in keep_fields or not compact:
        compact["summary"] = _source_text(response)
    return compact


def _fit_under_cap(response: dict[str, Any], max_response_tokens: int) -> dict[str, Any]:
    compact = dict(response)
    if response_token_count(compact) <= max_response_tokens:
        return compact
    summary = str(compact.get("summary", ""))
    while response_token_count(compact) > max_response_tokens and summary:
        next_len = max(0, int(len(summary) * 0.7))
        summary = summary[:next_len].rstrip()
        compact["summary"] = f"{summary}..." if summary else ""
    if response_token_count(compact) > max_response_tokens:
        compact["summary"] = ""
    if response_token_count(compact) > max_response_tokens:
        raise ValueError("compressed response still exceeds cap")
    return compact


def compress_response(response: Any, *, max_response_tokens: int, keep_fields: list[str]) -> Any:
    if response_token_count(response) <= max_response_tokens:
        return response
    return _fit_under_cap(_initial_extract(response, keep_fields), max_response_tokens)


def compress_response_fixtures(
    fixtures: Path,
    *,
    max_response_tokens: int,
    out_dir: Path,
    report_path: Path | None = None,
    keep_fields: list[str] | None = None,
    strategy: str = "extractive",
) -> dict[str, Any]:
    if strategy != "extractive":
        raise ValueError("only extractive compression is supported")
    fields = keep_fields or ["id", "title", "url", "state", "summary"]
    files = _fixture_files(fixtures)
    if not files:
        raise ValueError(f"no JSON fixtures found at {fixtures}")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for file in files:
        payload = json.loads(file.read_text(encoding="utf-8"))
        response = payload.get("response", payload) if isinstance(payload, dict) else payload
        before = response_token_count(response)
        status = "SKIPPED_UNDER_CAP"
        compressed = response
        if before > max_response_tokens:
            compressed = compress_response(
                response, max_response_tokens=max_response_tokens, keep_fields=fields
            )
            status = "COMPRESSED"
        after = response_token_count(compressed)
        output_payload = dict(payload) if isinstance(payload, dict) else {"response": payload}
        output_payload["response"] = compressed
        out_file = out_dir / file.name
        out_file.write_text(json.dumps(output_payload, indent=2, sort_keys=True) + "\n")
        rows.append(
            {
                "file": str(file),
                "out_file": str(out_file),
                "tool": payload.get("tool", file.stem) if isinstance(payload, dict) else file.stem,
                "status": status,
                "before_response_tokens": before,
                "after_response_tokens": after,
                "max_response_tokens": max_response_tokens,
            }
        )
    report = {
        "strategy": strategy,
        "max_response_tokens": max_response_tokens,
        "compressed": sum(1 for row in rows if row["status"] == "COMPRESSED"),
        "files": rows,
        "status": "PASS"
        if all(row["after_response_tokens"] <= max_response_tokens for row in rows)
        else "FAIL",
    }
    if report_path is not None:
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def run_compress_demo(*, max_response_tokens: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mcp-context-budget-compress-") as tmp:
        root = Path(tmp)
        fixtures = root / "responses"
        fixtures.mkdir()
        fixture = fixtures / "github_get_issue.json"
        response = {
            "id": 42,
            "title": "Parser bug",
            "url": "https://example.invalid/issues/42",
            "state": "open",
            "body": "oversized issue response payload " * 2500,
        }
        fixture.write_text(
            json.dumps({"tool": "github/get_issue", "response": response}) + "\n",
            encoding="utf-8",
        )
        report = compress_response_fixtures(
            fixtures,
            max_response_tokens=max_response_tokens,
            out_dir=root / "compressed",
            report_path=root / "report.json",
        )
    row = report["files"][0]
    return {
        "before_response_tokens": row["before_response_tokens"],
        "after_response_tokens": row["after_response_tokens"],
        "compression_status": report["status"],
    }

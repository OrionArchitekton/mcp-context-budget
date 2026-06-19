from __future__ import annotations

import re
import sqlite3

from mcp_context_budget.models import ToolRecord

_TERM_RE = re.compile(r"[A-Za-z0-9_]+")


def _terms(text: str) -> list[str]:
    return [term.lower() for term in _TERM_RE.findall(text)]


def _fallback_rank(tools: list[ToolRecord], task: str) -> list[ToolRecord]:
    query = set(_terms(task))

    def score(tool: ToolRecord) -> tuple[int, int, str]:
        text_terms = set(_terms(tool.search_text))
        overlap = len(query & text_terms)
        name_hits = sum(1 for term in query if term in tool.name.lower())
        return (overlap + name_hits * 2, -tool.schema_tokens, tool.tool_id)

    return sorted(tools, key=score, reverse=True)


def rank_tools(tools: list[ToolRecord], task: str) -> list[ToolRecord]:
    query_terms = _terms(task)
    if not query_terms:
        return sorted(tools, key=lambda tool: tool.tool_id)
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE tools USING fts5(tool_id, body)")
        for tool in tools:
            con.execute(
                "INSERT INTO tools(tool_id, body) VALUES (?, ?)", (tool.tool_id, tool.search_text)
            )
        query = " OR ".join(query_terms)
        rows = con.execute(
            "SELECT tool_id FROM tools WHERE tools MATCH ? ORDER BY bm25(tools) LIMIT ?",
            (query, len(tools)),
        ).fetchall()
        by_id = {tool.tool_id: tool for tool in tools}
        ranked = [by_id[row[0]] for row in rows if row[0] in by_id]
        ranked_ids = {tool.tool_id for tool in ranked}
        ranked.extend(
            tool for tool in _fallback_rank(tools, task) if tool.tool_id not in ranked_ids
        )
        return ranked
    except sqlite3.Error:
        return _fallback_rank(tools, task)


def select_tools(
    tools: list[ToolRecord], *, task: str, max_tools: int, max_schema_tokens: int
) -> list[ToolRecord]:
    selected: list[ToolRecord] = []
    total = 0
    for tool in rank_tools(tools, task):
        if len(selected) >= max_tools:
            break
        if total + tool.schema_tokens > max_schema_tokens:
            continue
        selected.append(tool)
        total += tool.schema_tokens
    if not selected:
        raise ValueError("no tools fit within the schema-token budget")
    return selected

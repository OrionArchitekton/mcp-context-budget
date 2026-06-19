from __future__ import annotations

import json
import math
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp_context_budget.models import ToolRecord
from mcp_context_budget.selector import select_tools


def _as_vector(raw: object, *, label: str) -> list[float]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"embedding vector for {label} must be a non-empty list")
    vector: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"embedding vector for {label} must contain only numbers")
        if not math.isfinite(value):
            raise ValueError(f"embedding vector for {label} must be finite (no NaN/Infinity)")
        vector.append(float(value))
    return vector


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding vectors must have matching dimensions")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _read_fixture_embeddings(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("embedding fixture must be a JSON object")
    if not isinstance(payload.get("queries"), dict) or not isinstance(payload.get("tools"), dict):
        raise ValueError("embedding fixture must contain `queries` and `tools` objects")
    return payload


def _fixture_query_vector(payload: dict[str, Any], task: str) -> list[float]:
    queries = payload["queries"]
    if task not in queries:
        raise ValueError(f"missing embedding vector for query {task}")
    return _as_vector(queries[task], label=f"query {task}")


def _fixture_tool_vector(payload: dict[str, Any], tool: ToolRecord) -> list[float]:
    tools = payload["tools"]
    if tool.tool_id not in tools:
        raise ValueError(f"missing embedding vector for tool {tool.tool_id}")
    return _as_vector(tools[tool.tool_id], label=f"tool {tool.tool_id}")


def _ollama_embedding(text: str, *, base_url: str, model: str) -> list[float]:
    url = base_url.rstrip("/") + "/api/embeddings"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Ollama URL must use http or https")
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - scheme is validated above.
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ValueError(f"Ollama embedding request failed: {exc}") from exc
    if not isinstance(body, dict):
        raise ValueError("Ollama embedding response is not a JSON object")
    if "embedding" in body:
        return _as_vector(body["embedding"], label="ollama response")
    embeddings = body.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        return _as_vector(embeddings[0], label="ollama response")
    raise ValueError("Ollama embedding response did not contain an embedding")


def rank_semantic_tools(
    tools: list[ToolRecord],
    *,
    task: str,
    embedding_backend: str,
    embedding_file: Path | None = None,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "nomic-embed-text",
) -> list[ToolRecord]:
    if embedding_backend == "fixture":
        if embedding_file is None:
            raise ValueError("--embedding-file is required for fixture embeddings")
        fixture = _read_fixture_embeddings(embedding_file)
        query_vector = _fixture_query_vector(fixture, task)
        scored = [
            (_cosine(query_vector, _fixture_tool_vector(fixture, tool)), tool.schema_tokens, tool)
            for tool in tools
        ]
    elif embedding_backend == "ollama":
        query_vector = _ollama_embedding(task, base_url=ollama_url, model=ollama_model)
        scored = [
            (
                _cosine(
                    query_vector,
                    _ollama_embedding(tool.search_text, base_url=ollama_url, model=ollama_model),
                ),
                tool.schema_tokens,
                tool,
            )
            for tool in tools
        ]
    else:
        raise ValueError(f"unsupported embedding backend: {embedding_backend}")
    return [row[2] for row in sorted(scored, key=lambda row: (-row[0], row[1], row[2].tool_id))]


def select_semantic_tools(
    tools: list[ToolRecord],
    *,
    task: str,
    max_tools: int,
    max_schema_tokens: int,
    embedding_backend: str,
    embedding_file: Path | None = None,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "nomic-embed-text",
) -> list[ToolRecord]:
    selected: list[ToolRecord] = []
    total = 0
    ranked = rank_semantic_tools(
        tools,
        task=task,
        embedding_backend=embedding_backend,
        embedding_file=embedding_file,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
    )
    for tool in ranked:
        if len(selected) >= max_tools:
            break
        if total + tool.schema_tokens > max_schema_tokens:
            continue
        selected.append(tool)
        total += tool.schema_tokens
    if not selected:
        raise ValueError("no semantic tools fit within the schema-token budget")
    return selected


def semantic_demo_records() -> tuple[list[ToolRecord], dict[str, Any]]:
    tools = [
        ToolRecord("github", "get_issue", "Retrieve a repository record by number", {}),
        ToolRecord("github", "list_issue_comments", "Diagnose bug report discussion comments", {}),
        ToolRecord("github", "update_issue", "Modify repository record metadata", {}),
        ToolRecord("linear", "search_ticket", "Find planning work items", {}),
    ]
    embeddings = {
        "queries": {"diagnose bug report": [1.0, 0.0]},
        "tools": {
            "github/get_issue": [1.0, 0.0],
            "github/list_issue_comments": [0.0, 1.0],
            "github/update_issue": [0.3, 0.7],
            "linear/search_ticket": [0.2, 0.8],
        },
    }
    return tools, embeddings


def run_semantic_demo(*, task: str, max_tools: int, max_schema_tokens: int) -> dict[str, Any]:
    tools, embeddings = semantic_demo_records()
    with tempfile.TemporaryDirectory(prefix="mcp-context-budget-semantic-") as tmp:
        embedding_file = Path(tmp) / "embeddings.json"
        embedding_file.write_text(json.dumps(embeddings) + "\n", encoding="utf-8")
        lexical_first = select_tools(
            tools, task=task, max_tools=1, max_schema_tokens=max_schema_tokens
        )[0]
        semantic_selected = select_semantic_tools(
            tools,
            task=task,
            max_tools=max_tools,
            max_schema_tokens=max_schema_tokens,
            embedding_backend="fixture",
            embedding_file=embedding_file,
        )
    return {
        "lexical_selected_tool": lexical_first.tool_id,
        "lexical_selected_wrong": lexical_first.tool_id != "github/get_issue",
        "semantic_selected_tool": semantic_selected[0].tool_id,
        "semantic_selected_tools": [tool.tool_id for tool in semantic_selected],
        "semantic_status": "PASS"
        if semantic_selected and semantic_selected[0].tool_id == "github/get_issue"
        else "FAIL",
    }

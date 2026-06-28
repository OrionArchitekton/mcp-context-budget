from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from concurrent.futures import as_completed
from unittest.mock import patch

import pytest

from mcp_context_budget.models import ToolRecord
from mcp_context_budget.semantic import (
    _ollama_embeddings_parallel,
    prove_parallel_ollama_batching,
    rank_semantic_tools,
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_context_budget", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def write_semantic_fixture(tmp_path: Path) -> tuple[Path, Path]:
    tool_list = tmp_path / "tools.json"
    tool_list.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "github",
                        "tools": [
                            {
                                "name": "get_issue",
                                "description": "Retrieve a repository record by number",
                                "inputSchema": {"type": "object"},
                            },
                            {
                                "name": "list_issue_comments",
                                "description": "Diagnose bug report discussion comments",
                                "inputSchema": {"type": "object"},
                            },
                        ],
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    embeddings = tmp_path / "embeddings.json"
    embeddings.write_text(
        json.dumps(
            {
                "queries": {"diagnose bug report": [1.0, 0.0]},
                "tools": {
                    "github/get_issue": [1.0, 0.0],
                    "github/list_issue_comments": [0.0, 1.0],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return tool_list, embeddings


def test_semantic_select_beats_lexical_on_planted_synonym_case(tmp_path: Path) -> None:
    tool_list, embeddings = write_semantic_fixture(tmp_path)
    lock_path = tmp_path / "semantic.lock.json"

    result = run_cli(
        "semantic-select",
        "--tool-list",
        str(tool_list),
        "--task",
        "diagnose bug report",
        "--max-tools",
        "1",
        "--max-schema-tokens",
        "1000",
        "--embedding-backend",
        "fixture",
        "--embedding-file",
        str(embeddings),
        "--out-lock",
        str(lock_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["selected_tools"] == ["github/get_issue"]
    assert "SEMANTIC_SELECTED_TOOL=github/get_issue" in result.stdout


def test_semantic_select_fails_closed_when_fixture_vector_is_missing(tmp_path: Path) -> None:
    tool_list, embeddings = write_semantic_fixture(tmp_path)
    payload = json.loads(embeddings.read_text(encoding="utf-8"))
    del payload["tools"]["github/get_issue"]
    embeddings.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = run_cli(
        "semantic-select",
        "--tool-list",
        str(tool_list),
        "--task",
        "diagnose bug report",
        "--embedding-backend",
        "fixture",
        "--embedding-file",
        str(embeddings),
    )

    assert result.returncode == 2
    assert "missing embedding vector for tool github/get_issue" in result.stderr


@pytest.mark.parametrize(
    ("case", "texts", "max_workers", "expect_peak_ge", "expect_error"),
    [
        ("batching", ["alpha one", "beta two", "gamma three", "delta four"], None, 2, None),
        ("worker_cap", ["t0", "t1", "t2", "t3"], 2, 2, None),
        ("single_tool_error", ["alpha ok", "beta fail"], None, 0, "simulated outage"),
        ("multiple_tool_errors", ["alpha fail", "beta fail"], None, 0, "batch fail"),
        ("query_embedding_error", ["alpha ok"], None, 0, "query fail"),
    ],
)
def test_parallel_ollama_modes(
    case: str,
    texts: list[str],
    max_workers: int | None,
    expect_peak_ge: int,
    expect_error: str | None,
) -> None:
    lock = threading.Lock()
    active = 0
    peak = 0

    def fake_embedding(text: str, *, base_url: str, model: str) -> list[float]:
        nonlocal active, peak
        if case == "query_embedding_error" and text == "query task":
            raise ValueError("query fail")
        if expect_error and "fail" in text:
            raise ValueError(expect_error)
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return [1.0, 0.0]
        finally:
            with lock:
                active -= 1

    with patch("mcp_context_budget.semantic._ollama_embedding", side_effect=fake_embedding):
        if case == "query_embedding_error":
            tools = [ToolRecord("github", "alpha", "alpha tool", {})]
            with pytest.raises(ValueError, match="query fail"):
                rank_semantic_tools(tools, task="query task", embedding_backend="ollama")
            return

        if expect_error:
            with pytest.raises(ValueError, match=expect_error):
                if case == "single_tool_error":
                    tools = [
                        ToolRecord("github", "alpha", "alpha ok", {}),
                        ToolRecord("github", "beta", "beta fail", {}),
                    ]
                    rank_semantic_tools(tools, task="alpha task", embedding_backend="ollama")
                else:
                    _ollama_embeddings_parallel(
                        texts, base_url="http://localhost:11434", model="m", max_workers=max_workers
                    )
            return

        vectors = _ollama_embeddings_parallel(
            texts,
            base_url="http://localhost:11434",
            model="m",
            max_workers=max_workers,
        )
        assert len(vectors) == len(texts)
        assert peak >= expect_peak_ge
        if case == "worker_cap":
            assert peak <= 2


def test_parallel_batch_raises_on_incomplete_results() -> None:
    real_as_completed = as_completed

    def one_future_only(futures, timeout=None):
        iterator = real_as_completed(futures, timeout=timeout)
        yield next(iterator)

    with patch("mcp_context_budget.semantic.as_completed", side_effect=one_future_only):
        with patch("mcp_context_budget.semantic._ollama_embedding", return_value=[1.0, 0.0]):
            with pytest.raises(ValueError, match="incomplete results"):
                _ollama_embeddings_parallel(
                    ["alpha", "beta"],
                    base_url="http://localhost:11434",
                    model="m",
                )


def test_rank_semantic_tools_preserves_ordering_after_parallel_batch() -> None:
    tools = [
        ToolRecord("github", "alpha", "alpha tool", {}),
        ToolRecord("github", "beta", "beta tool", {}),
        ToolRecord("github", "gamma", "gamma tool", {}),
    ]

    def fake_embedding(text: str, *, base_url: str, model: str) -> list[float]:
        if "alpha" in text:
            return [1.0, 0.0]
        if "beta" in text:
            return [0.5, 0.5]
        return [0.0, 1.0]

    with patch("mcp_context_budget.semantic._ollama_embedding", side_effect=fake_embedding):
        ranked = rank_semantic_tools(tools, task="alpha task", embedding_backend="ollama")

    assert ranked[0].tool_id == "github/alpha"


def test_prove_parallel_ollama_batching_passes() -> None:
    proof = prove_parallel_ollama_batching()
    assert proof["status"] == "PASS"
    assert proof["batched"] is True


def test_semantic_demo_fixture_backend_skips_parallel_proof() -> None:
    result = run_cli(
        "semantic-demo",
        "--task",
        "diagnose bug report",
        "--max-tools",
        "3",
        "--max-schema-tokens",
        "3000",
        "--embedding-backend",
        "fixture",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert lines["SEMANTIC_SELECT_STATUS"] == "PASS"
    assert lines["PARALLEL_OLLAMA_BATCHED"] == "skipped"


def test_prove_parallel_ollama_demo_prints_batched_true() -> None:
    result = run_cli("prove-parallel-ollama-demo")

    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert lines["PARALLEL_OLLAMA_BATCHED"] == "true"
    assert lines["PARALLEL_OLLAMA_PROOF_STATUS"] == "PASS"

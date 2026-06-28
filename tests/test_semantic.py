from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path
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


def test_parallel_batch_fills_every_slot_in_order_under_bounded_window() -> None:
    # Completeness invariant: with more texts than workers, the bounded in-flight
    # window must still embed every text and return results positionally aligned to
    # the input order (so no slot is left unfilled / fail-closed guard never trips).
    texts = [f"text-{i}" for i in range(7)]

    def fake_embedding(text: str, *, base_url: str, model: str) -> list[float]:
        n = float(text.split("-")[1])
        return [n, 0.0]

    with patch("mcp_context_budget.semantic._ollama_embedding", side_effect=fake_embedding):
        vectors = _ollama_embeddings_parallel(
            texts,
            base_url="http://localhost:11434",
            model="m",
            max_workers=2,
        )

    assert vectors == [[float(i), 0.0] for i in range(7)]


def test_parallel_batch_stops_submitting_after_embedding_error() -> None:
    # Deterministic fail-closed contract (no scheduling luck): with a bounded
    # in-flight window of `max_workers`, work beyond the first wave is only
    # submitted after a successful completion. When a request in the first wave
    # fails, the not-yet-submitted request is never started.
    hold = threading.Event()
    calls: list[str] = []
    submitted_snapshots: list[list[Future[list[float]]]] = []

    def fake_embedding(text: str, *, base_url: str, model: str) -> list[float]:
        calls.append(text)
        if text == "fail":
            raise ValueError("batch fail")
        hold.wait(timeout=2)
        return [1.0, 0.0]

    texts = ["hold-a", "hold-b", "fail", "later"]
    with patch("mcp_context_budget.semantic._ollama_embedding", side_effect=fake_embedding):
        with pytest.raises(ValueError, match="batch fail"):
            _ollama_embeddings_parallel(
                texts,
                base_url="http://localhost:11434",
                model="m",
                max_workers=3,
                on_futures=submitted_snapshots.append,
            )

    assert "fail" in calls
    # The fourth text was never submitted, so its request never started.
    assert "later" not in calls
    submitted = submitted_snapshots[-1]
    assert len(submitted) == 3
    # Already-running first-wave requests are not cancelled (they were executing);
    # only never-submitted work is withheld.
    assert all(not future.cancelled() for future in submitted)


def test_parallel_batch_error_returns_promptly_without_waiting_on_running() -> None:
    # A running embedding that blocks must NOT delay the caller's observation of a
    # fast failure. Re-raising inside `with ThreadPoolExecutor(...)` would join the
    # blocking request (shutdown wait=True) before the error surfaced. With
    # shutdown(wait=False, cancel_futures=True) the caller must see the failure
    # well before the slow request would have finished.
    release = threading.Event()

    def fake_embedding(text: str, *, base_url: str, model: str) -> list[float]:
        if text == "fail":
            raise ValueError("fast failure")
        if text == "block":
            # Would hold the caller for ~5s if shutdown waited on running work.
            release.wait(timeout=5)
        return [1.0, 0.0]

    texts = ["block", "fail"]
    start = time.monotonic()
    try:
        with patch("mcp_context_budget.semantic._ollama_embedding", side_effect=fake_embedding):
            with pytest.raises(ValueError, match="fast failure"):
                _ollama_embeddings_parallel(
                    texts,
                    base_url="http://localhost:11434",
                    model="m",
                    max_workers=2,
                )
        elapsed = time.monotonic() - start
        # Comfortably under the 5s block: proves we did not join the running request.
        assert elapsed < 2.0, f"error path blocked on running request for {elapsed:.2f}s"
    finally:
        release.set()


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

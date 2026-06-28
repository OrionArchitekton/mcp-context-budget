from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

from mcp_context_budget.models import ToolRecord
from mcp_context_budget.semantic import prove_parallel_ollama_batching, rank_semantic_tools


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


def test_parallel_ollama_uses_thread_pool_batching() -> None:
    tools = [
        ToolRecord("github", "alpha", "alpha tool", {}),
        ToolRecord("github", "beta", "beta tool", {}),
        ToolRecord("github", "gamma", "gamma tool", {}),
        ToolRecord("github", "delta", "delta tool", {}),
    ]
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_embedding(text: str, *, base_url: str, model: str) -> list[float]:
        import time

        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return [1.0, 0.0] if "alpha" in text else [0.0, 1.0]
        finally:
            with lock:
                active -= 1

    with patch("mcp_context_budget.semantic._ollama_embedding", side_effect=fake_embedding):
        ranked = rank_semantic_tools(
            tools,
            task="alpha task",
            embedding_backend="ollama",
        )

    assert peak >= 2
    assert len(ranked) == len(tools)


def test_prove_parallel_ollama_batching_passes() -> None:
    proof = prove_parallel_ollama_batching()
    assert proof["status"] == "PASS"
    assert proof["batched"] is True


def test_semantic_demo_prints_new_capability_proof() -> None:
    result = run_cli(
        "semantic-demo",
        "--task",
        "diagnose bug report",
        "--max-tools",
        "3",
        "--max-schema-tokens",
        "3000",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert lines["LEXICAL_SELECTED_WRONG"] == "true"
    assert lines["SEMANTIC_SELECTED_TOOL"] == "github/get_issue"
    assert lines["SEMANTIC_STATUS"] == "PASS"
    assert lines["SEMANTIC_SELECT_STATUS"] == "PASS"
    assert lines["PARALLEL_OLLAMA_BATCHED"] == "true"

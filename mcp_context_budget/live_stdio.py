from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any

REDACTION = "<redacted>"


@dataclass(frozen=True)
class LiveToolsResult:
    server: str
    tools: list[dict[str, Any]]


class _ByteBudget:
    def __init__(self, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("--max-stdio-bytes must be greater than zero")
        self.max_bytes = max_bytes
        self.used = 0

    def add(self, count: int) -> None:
        self.used += count
        if self.used > self.max_bytes:
            raise ValueError("stdio byte limit exceeded while reading MCP server output")


def redact_text(text: str, env: object) -> str:
    redacted = text
    if isinstance(env, dict):
        # Replace longer values first: a secret that is a substring of another
        # must not survive as a partial leak after the longer one is redacted.
        values = sorted(
            (v for v in env.values() if isinstance(v, str) and v), key=len, reverse=True
        )
        for value in values:
            redacted = redacted.replace(value, REDACTION)
    return redacted


def _message_bytes(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def _write_message(process: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise ValueError("MCP server stdin is unavailable")
    process.stdin.write(_message_bytes(payload))
    process.stdin.flush()


def _read_from_fd(
    selector: selectors.BaseSelector,
    fd: int,
    *,
    deadline: float,
    max_len: int,
    budget: _ByteBudget,
) -> bytes:
    chunks: list[bytes] = []
    remaining = max_len
    while remaining > 0:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise TimeoutError("timed out waiting for MCP server output")
        events = selector.select(timeout)
        if not events:
            raise TimeoutError("timed out waiting for MCP server output")
        chunk = os.read(fd, min(remaining, 4096))
        if not chunk:
            raise ValueError("MCP server exited before sending a complete response")
        chunks.append(chunk)
        budget.add(len(chunk))
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_until_header_end(
    selector: selectors.BaseSelector,
    fd: int,
    *,
    deadline: float,
    budget: _ByteBudget,
) -> bytes:
    header = bytearray()
    while b"\r\n\r\n" not in header and b"\n\n" not in header:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise TimeoutError("timed out waiting for MCP response headers")
        events = selector.select(timeout)
        if not events:
            raise TimeoutError("timed out waiting for MCP response headers")
        chunk = os.read(fd, 1)
        if not chunk:
            raise ValueError("MCP server exited before response headers")
        header.extend(chunk)
        budget.add(len(chunk))
        if len(header) > 8192:
            raise ValueError("MCP response headers exceeded 8192 bytes")
    return bytes(header)


def _read_message(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    *,
    deadline: float,
    budget: _ByteBudget,
) -> dict[str, Any]:
    if process.stdout is None:
        raise ValueError("MCP server stdout is unavailable")
    fd = process.stdout.fileno()
    header = _read_until_header_end(selector, fd, deadline=deadline, budget=budget)
    separator = b"\r\n\r\n" if b"\r\n\r\n" in header else b"\n\n"
    raw_header, body_prefix = header.split(separator, 1)
    content_length: int | None = None
    for raw_line in raw_header.replace(b"\r\n", b"\n").split(b"\n"):
        name, _, value = raw_line.partition(b":")
        if name.strip().lower() == b"content-length":
            try:
                content_length = int(value.strip())
            except ValueError as exc:
                raise ValueError("MCP response has invalid Content-Length") from exc
    if content_length is None:
        raise ValueError("MCP response missing Content-Length header")
    if content_length < len(body_prefix):
        raise ValueError("MCP response body exceeded Content-Length")
    body = body_prefix
    if len(body) < content_length:
        body += _read_from_fd(
            selector,
            fd,
            deadline=deadline,
            max_len=content_length - len(body),
            budget=budget,
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("MCP response body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("MCP response body is not a JSON object")
    return payload


def _read_response(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    *,
    expected_id: int,
    deadline: float,
    budget: _ByteBudget,
) -> dict[str, Any]:
    while True:
        payload = _read_message(process, selector, deadline=deadline, budget=budget)
        if payload.get("id") != expected_id:
            continue
        if "error" in payload:
            raise ValueError(f"MCP server returned error for request {expected_id}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"MCP server response {expected_id} missing result object")
        return result


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def introspect_server_tools(
    *,
    server: str,
    command: object,
    args: object,
    env: object,
    start_timeout_seconds: float = 5.0,
    max_stdio_bytes: int = 65536,
) -> LiveToolsResult:
    if not isinstance(command, str) or not command:
        raise ValueError(f"server {server} cannot be started: command is missing")
    argv = [command]
    if isinstance(args, list):
        argv.extend(str(arg) for arg in args)
    if start_timeout_seconds <= 0:
        raise ValueError("--start-timeout-seconds must be greater than zero")
    child_env = os.environ.copy()
    if isinstance(env, dict):
        child_env.update({str(key): str(value) for key, value in env.items()})
    process: subprocess.Popen[bytes] | None = None
    # Capture the child's stderr to a temp file (NOT sys.stderr directly): a
    # misbehaving server can echo its own injected secrets to stderr, and
    # passing the stream through raw would leak them. A temp file (vs a PIPE)
    # avoids any pipe-buffer deadlock while we synchronously read stdout. On
    # cleanup we redact the configured env values before forwarding.
    stderr_capture = tempfile.TemporaryFile(mode="w+b")
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is explicit and shell=False.
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_capture,
            env=child_env,
            shell=False,
        )
        if process.stdout is None:
            raise ValueError("MCP server stdout is unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + start_timeout_seconds
        budget = _ByteBudget(max_stdio_bytes)
        _write_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-context-budget", "version": "0.3.0"},
                },
            },
        )
        _read_response(process, selector, expected_id=1, deadline=deadline, budget=budget)
        _write_message(
            process,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        request_id = 2
        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            _write_message(
                process,
                {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": params},
            )
            result = _read_response(
                process, selector, expected_id=request_id, deadline=deadline, budget=budget
            )
            raw_tools = result.get("tools")
            if not isinstance(raw_tools, list):
                raise ValueError("MCP tools/list result missing tools array")
            # Fail closed: a non-object entry means the server's listing is
            # malformed; silently dropping it could hide a real tool from the budget.
            for tool in raw_tools:
                if not isinstance(tool, dict):
                    raise ValueError("MCP tools/list returned a non-object tool entry")
            tools.extend(raw_tools)
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
            request_id += 1
        return LiveToolsResult(server=server, tools=tools)
    except TimeoutError as exc:
        raise ValueError(f"timed out starting MCP server {server}") from exc
    except OSError as exc:
        raise ValueError(f"failed to start MCP server {server}: {exc}") from exc
    finally:
        if process is not None:
            _terminate_process(process)
        try:
            stderr_capture.seek(0)
            captured = stderr_capture.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            captured = ""
        finally:
            stderr_capture.close()
        if captured:
            sys.stderr.write(redact_text(captured, env))
            sys.stderr.flush()


def run_fixture_mcp_server(*, mode: str = "ok") -> int:
    if mode == "hang":
        time.sleep(60)
        return 0
    if mode == "garbage":
        sys.stdout.buffer.write(b"not-json\n")
        sys.stdout.buffer.flush()
        return 0

    def read_one() -> dict[str, Any] | None:
        header = b""
        while b"\r\n\r\n" not in header:
            byte = sys.stdin.buffer.read(1)
            if not byte:
                return None
            header += byte
        raw_header, body_prefix = header.split(b"\r\n\r\n", 1)
        length = 0
        for raw_line in raw_header.split(b"\r\n"):
            name, _, value = raw_line.partition(b":")
            if name.lower() == b"content-length":
                length = int(value.strip())
        body = body_prefix + sys.stdin.buffer.read(length - len(body_prefix))
        return json.loads(body.decode("utf-8"))

    def send(payload: dict[str, Any]) -> None:
        sys.stdout.buffer.write(_message_bytes(payload))
        sys.stdout.buffer.flush()

    tools = [
        {
            "name": "safe_read",
            "description": "Read a safe local fixture",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
        {
            "name": "danger_delete",
            "description": "Delete a local fixture",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    ]
    if mode == "stderr-secret":
        sys.stderr.write(os.environ.get("DEMO_SECRET", "") + "\n")
        sys.stderr.flush()
    if mode == "large":
        tools[0]["description"] = "x" * 10000
    initialized = False
    while True:
        message = read_one()
        if message is None:
            return 0
        method = message.get("method")
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "fixture", "version": "0.1.0"},
                    },
                }
            )
            if mode == "exit-before-tools":
                return 0
        elif method == "notifications/initialized":
            initialized = True
        elif method == "tools/list":
            if not initialized:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32002, "message": "not initialized"},
                    }
                )
            else:
                send({"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": tools}})


def run_allow_start_demo(
    *,
    start_timeout_seconds: float = 2.0,
    max_stdio_bytes: int = 65536,
) -> dict[str, Any]:
    import tempfile
    from pathlib import Path

    from mcp_context_budget.config_edit import apply_config_selection
    from mcp_context_budget.loaders import load_mcp_config
    from mcp_context_budget.models import fingerprint_tool_ids

    with tempfile.TemporaryDirectory(prefix="mcp-context-budget-allow-start-") as tmp:
        root = Path(tmp)
        config = root / "mcp.json"
        lock = root / "lock.json"
        materialized = root / "materialized"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "fixture": {
                            "command": sys.executable,
                            "args": ["-m", "mcp_context_budget", "_fixture-mcp-server"],
                            "env": {"DEMO_SECRET": "demo-secret-value"},
                        }
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        lock.write_text(json.dumps({"selected_tools": ["fixture/safe_read"]}) + "\n")
        before = apply_config_selection(config_path=config, lock_path=lock, write=False)
        records, _ = load_mcp_config(
            config,
            allow_start=True,
            start_timeout_seconds=start_timeout_seconds,
            max_stdio_bytes=max_stdio_bytes,
        )
        lock.write_text(
            json.dumps(
                {
                    "selected_tools": ["fixture/safe_read"],
                    "config_fingerprint": fingerprint_tool_ids(r.tool_id for r in records),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        after = apply_config_selection(
            config_path=config,
            lock_path=lock,
            write=True,
            backup_dir=root / "backups",
            allow_start=True,
            start_timeout_seconds=start_timeout_seconds,
            max_stdio_bytes=max_stdio_bytes,
            materialize_tools_list=materialized,
        )
        rescan_records, _ = load_mcp_config(config, allow_start=False)
        remaining = {record.tool_id for record in rescan_records}
        payload = json.loads(config.read_text(encoding="utf-8"))
        tools_path = payload["mcpServers"]["fixture"].get("toolsListPath")
    return {
        "before_config_not_patchable": len(before["not_patchable"]),
        "live_tools_listed": len(records),
        "materialized_tool_list": bool(tools_path),
        "after_config_not_patchable": len(after["not_patchable"]),
        "live_introspection_status": (
            "PASS"
            if after["status"] == "PASS"
            and "fixture/safe_read" in remaining
            and "fixture/danger_delete" not in remaining
            else "FAIL"
        ),
    }

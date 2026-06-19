# Contributing

Thanks for helping improve `mcp-context-budget`.

## Local Setup

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check .
```

## Development Rules

- Keep runtime dependencies small and OSS-only.
- Do not add private service dependencies.
- Add or update tests for behavior changes.
- Keep the Docker demo real: it must exercise scan, select, and check.
- Do not commit secrets or real MCP credentials. Config reports must redact
  environment values.

## Pull Requests

Describe the user-facing behavior change, include test output, and call out any
new dependency or CLI compatibility concern.

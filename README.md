# mcp-context-budget

Local-first MCP context budget and tool-selection verifier for agentic coding
environments.

MCP servers can load enough tool schema and response data to burn a large
fraction of an agent context window before useful work starts. This tool gives
developers a repeatable budget gate:

- scan MCP config or `tools/list` fixtures
- estimate schema and response-token cost
- select a smaller task-relevant tool set with deterministic SQLite FTS5/BM25
- write a lockfile for CI
- fail builds when schema or response budgets regress
- prove the spine with a Docker demo

No private Orion services are required. v1 has no external runtime service
dependency.

## Install

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## Quick Demo

```bash
mcp-context-budget demo \
  --task "triage a GitHub issue and update one ticket" \
  --max-tools 8 \
  --max-schema-tokens 6000 \
  --max-response-tokens 4000
```

Expected spine proof:

```text
DEMO_CATALOG_SERVERS=5
DEMO_CATALOG_TOOLS=120
BEFORE_SCHEMA_TOKENS=<large>
SELECTED_TOOLS=<8-or-less>
AFTER_SCHEMA_TOKENS=<cap-or-less>
OVERSIZED_RESPONSE_FIXTURE=flagged
BUDGET_STATUS=PASS
```

## Commands

```bash
mcp-context-budget scan --tool-list fixtures/demo-tools.json --out mcp-budget.report.md --lock-out mcp-budget.lock.json
mcp-context-budget select --tool-list fixtures/demo-tools.json --task "triage a GitHub issue" --max-tools 8 --max-schema-tokens 6000 --out-lock mcp-budget.lock.json
mcp-context-budget check --lock mcp-budget.lock.json --max-schema-tokens 6000 --max-response-tokens 4000
mcp-context-budget export --lock mcp-budget.lock.json --format sarif --out mcp-budget.sarif
```

`scan --config` supports Claude/Cursor/Codex-style JSON with an `mcpServers`
object. Server entries may include `toolsListPath` to point at a recorded
`tools/list` JSON fixture. Environment values are redacted in reports.

`--allow-start` is intentionally conservative in v1. The tool prints the exact
stdio command that would be started, with environment values redacted, and
refuses live process startup unless future v0.2 work adds a hardened MCP
transport harness.

## Docker

```bash
docker build -t mcp-context-budget:local .
docker run --rm mcp-context-budget:local demo \
  --task "triage a GitHub issue and update one ticket" \
  --max-tools 8 \
  --max-schema-tokens 6000 \
  --max-response-tokens 4000
```

The image exposes no service port.

## Deferred to v0.2

- Live runtime MCP proxy/gateway that intercepts and routes actual tool calls.
- Embedding-based semantic selector using Ollama or pgvector.
- Browser UI.
- Direct edits to user MCP configs.
- Organization-wide background scanner.
- Vendor-specific hosted dashboards.
- Secret scanning beyond redaction and no-print checks.
- Automatic response compression for arbitrary live MCP servers.

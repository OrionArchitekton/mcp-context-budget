# mcp-context-budget

Local-first MCP context budget and tool-selection verifier for agentic coding
environments.

MCP servers can load enough tool schema and response data to burn a large
fraction of an agent context window before useful work starts. This tool gives
developers a repeatable budget gate:

- scan MCP config or `tools/list` fixtures
- estimate schema and response-token cost
- select a smaller task-relevant tool set with deterministic SQLite FTS5/BM25
- optionally prove semantic tool selection from fixture or Ollama embeddings
- write a lockfile for CI
- fail builds when schema or response budgets regress
- compress recorded response fixtures under a response budget
- apply selected-tool locks back to caller-owned MCP config files
- prove the spine with a Docker demo

No private Orion services are required. The core CLI has no external runtime
service dependency. Semantic selection can optionally call Ollama only when the
`--embedding-backend ollama` flag is explicitly selected.

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
mcp-context-budget semantic-select --tool-list fixtures/demo-tools.json --task "triage a GitHub issue" --embedding-backend fixture --embedding-file embeddings.json --out-lock mcp-budget.lock.json
mcp-context-budget check --lock mcp-budget.lock.json --max-schema-tokens 6000 --max-response-tokens 4000
mcp-context-budget compress-responses --fixtures responses/ --max-response-tokens 4000 --out-dir compressed-responses --report compression-report.json
mcp-context-budget config-apply --config mcp.json --lock mcp-budget.lock.json --dry-run --patch-out mcp-config.patch.json
mcp-context-budget export --lock mcp-budget.lock.json --format sarif --out mcp-budget.sarif
```

`scan --config` supports Claude/Cursor/Codex-style JSON with an `mcpServers`
object. Server entries may include `toolsListPath` to point at a recorded
`tools/list` JSON fixture. Environment values are redacted in reports.

`--allow-start` is intentionally conservative. The tool prints the exact
stdio command that would be started, with environment values redacted, and
refuses live process startup unless future work adds a hardened MCP
transport harness.

### Semantic Selection

`semantic-select` keeps the v0.1 lockfile shape but ranks tools by embedding
similarity before applying `--max-tools` and `--max-schema-tokens`.

Fixture mode is deterministic and requires no service:

```bash
mcp-context-budget semantic-select \
  --tool-list tools.json \
  --task "diagnose bug report" \
  --embedding-backend fixture \
  --embedding-file embeddings.json \
  --out-lock semantic.lock.json
```

The fixture file must contain:

```json
{
  "queries": {"diagnose bug report": [1.0, 0.0]},
  "tools": {"github/get_issue": [1.0, 0.0]}
}
```

Ollama mode uses stdlib HTTP and adds no Python package dependency:

```bash
mcp-context-budget semantic-select \
  --tool-list tools.json \
  --task "diagnose bug report" \
  --embedding-backend ollama \
  --ollama-url http://localhost:11434 \
  --ollama-model nomic-embed-text
```

### Response Fixture Compression

`compress-responses` reads one response fixture or a directory of `*.json`
fixtures, writes compressed copies, and emits a JSON report.

```bash
mcp-context-budget compress-responses \
  --fixtures responses/ \
  --max-response-tokens 4000 \
  --out-dir compressed-responses \
  --report compression-report.json
```

The v0.2 strategy is deterministic extractive compression. It preserves common
identifier fields and writes a `summary` when large body fields need to be cut.

### Config Apply

`config-apply` turns a selected-tool lock into a safe local MCP config patch.
Dry-run is the default posture; `--write` is required before the config file is
changed, and write mode creates a backup.

```bash
mcp-context-budget config-apply \
  --config mcp.json \
  --lock mcp-budget.lock.json \
  --mode disable-unselected \
  --dry-run \
  --patch-out mcp-config.patch.json

mcp-context-budget config-apply \
  --config mcp.json \
  --lock mcp-budget.lock.json \
  --mode disable-unselected \
  --write \
  --backup-dir backups/
```

Reports redact environment values.

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

v0.2 also includes independent Docker proof commands for the new capabilities:

```bash
docker run --rm mcp-context-budget:local semantic-demo \
  --task "diagnose bug report" \
  --max-tools 3 \
  --max-schema-tokens 3000
docker run --rm mcp-context-budget:local compress-demo --max-response-tokens 4000
docker run --rm mcp-context-budget:local config-demo
```

## Deferred to v0.3

- Live runtime MCP proxy/gateway that intercepts and routes actual tool calls.
- Browser UI.
- Organization-wide background scanner.
- Vendor-specific hosted dashboards.
- Secret scanning beyond redaction and no-print checks.
- Automatic response compression for arbitrary live MCP servers.

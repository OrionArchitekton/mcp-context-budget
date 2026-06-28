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
- opt into local stdio `tools/list` introspection for command-discovered servers
- audit MCP configs for plaintext secret exposure without printing values
- prove the spine with a Docker demo

No private Orion services are required. The core CLI has no external runtime
service dependency. Semantic selection can optionally call Ollama only when the
`--embedding-backend ollama` flag is explicitly selected. Live stdio
introspection can optionally start a caller-owned local MCP command only when
`--allow-start` is explicitly selected.

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
mcp-context-budget config-audit --config mcp.json --json-out mcp-config-audit.json --fail-on high
mcp-context-budget export --lock mcp-budget.lock.json --format sarif --out mcp-budget.sarif
```

`scan --config` supports Claude/Cursor/Codex-style JSON with an `mcpServers`
object. Server entries may include `toolsListPath` to point at a recorded
`tools/list` JSON fixture. Server entries may also include `stdioFraming`
(`auto`, `json-lines`, or `content-length`) when a local stdio server needs a
fixed transport framing. Environment values are redacted in reports.

`--allow-start` is intentionally conservative. It is never implied by default,
never required for static `toolsListPath` or inline-tool configs, and never
starts a hosted service. When explicitly selected, the tool starts the
caller-owned local stdio command as argv with `shell=False`, sends MCP
`initialize` and `tools/list`, enforces timeout and stdio-byte caps, redacts env
metadata, and exits the process after listing tools. The default
`--stdio-framing auto` prefers the current MCP SDK JSON-lines stdio transport
and falls back to the legacy `Content-Length` fixture transport; pass
`--stdio-framing json-lines` or `--stdio-framing content-length` to force one.

For command-discovered servers that need to become enforceable by
`config-apply`, combine `--allow-start` with `--materialize-tools-list`:

```bash
mcp-context-budget config-apply \
  --config mcp.json \
  --lock mcp-budget.lock.json \
  --write \
  --allow-start \
  --start-timeout-seconds 2 \
  --max-stdio-bytes 65536 \
  --stdio-framing auto \
  --materialize-tools-list materialized-tools/
```

This writes a local `toolsListPath` sidecar for the discovered tools, applies
the selected-tool lock there, and leaves later `scan`/`select` runs static again.

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

The apply contract is enforced, not advisory:

- **Inline and `toolsListPath` tools are both patched.** A server whose tools
  live in an external `tools/list` JSON has that file patched (and backed up)
  too — not silently skipped.
- **The lock is bound to the config.** Each lock records a `config_fingerprint`
  of its tool universe; `config-apply` refuses a lock whose fingerprint does not
  match the target config (a foreign/stale lock would otherwise disable every
  tool and still report success). Override with `--allow-fingerprint-mismatch`.
- **Honest status, never a false PASS.** A command-discovered server (no inline
  `tools`, no `toolsListPath`) cannot be enforced without live startup, so it is
  reported under `not_patchable` and the status is `PARTIAL`, not `PASS`.
- **Opt-in materialization closes the PARTIAL gap.** With `--allow-start` and
  `--materialize-tools-list`, command-discovered tools are listed through local
  stdio, saved to a caller-owned sidecar, and enforced as a normal
  `toolsListPath` catalog.
- **Disabling takes effect.** The loader honors `enabled: false`, so a disabled
  tool (or server) drops out of the budget on the next `scan`/`select`.

### Config Secret Audit

`config-audit` is a read-only hygiene check for MCP config files. It flags
high-confidence literal secrets in env values, args, and nested config fields,
while treating `${TOKEN}` references, `op://...` references, and redacted
placeholders as safe references.

```bash
mcp-context-budget config-audit \
  --config mcp.json \
  --json-out mcp-config-audit.json \
  --fail-on high
```

Reports include the config path, finding path, severity, secret class, length
bucket, and a short fingerprint. Literal secret values are never printed.

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
docker run --rm mcp-context-budget:local allow-start-demo --start-timeout-seconds 2 --max-stdio-bytes 65536 --stdio-framing auto
docker run --rm mcp-context-budget:local live-compress-demo --max-response-tokens 4000 --start-timeout-seconds 2
docker run --rm mcp-context-budget:local config-audit-demo
docker run --rm mcp-context-budget:local config-multiserver-demo
```

Expected v0.3 proof lines:

```text
LIVE_INTROSPECTION_STATUS=PASS
AFTER_CONFIG_NOT_PATCHABLE=0
CONFIG_AUDIT_STATUS=PASS
CONFIG_AUDIT_SECRET_VALUES_REDACTED=true
CONFIG_MULTISERVER_STATUS=PASS
```

## Out of v0.3

### Locked Out

- Live runtime MCP proxy/gateway that intercepts and routes actual tool calls.
- Browser UI.
- Organization-wide background scanner.
- Vendor-specific hosted dashboards.

These are not v0.4 commitments; they break the local-first CLI verifier shape.

### Shipped in v0.4

- `live-compress-demo` — opt-in bounded sampling of a live stdio tool response
  during fixture-proven startup, then extractive compression under a response
  token cap. Not a proxy; not default CI.

### Deferred to v0.5

- Parallelized Ollama embeddings and broader CLI polish.

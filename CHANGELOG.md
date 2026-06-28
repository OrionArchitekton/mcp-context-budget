# Changelog

## 0.4.0 - 2026-06-28

- Release the post-v0.3.0 JSON-lines / Content-Length stdio framing compat for
  `--allow-start` introspection. `allow-start-demo` now prints
  `STDIO_FRAMING_*=PASS` proof lines without changing the transport
  implementation.
- Parallelize Ollama embeddings in `semantic-select` via bounded stdlib
  `ThreadPoolExecutor` batching when `--embedding-backend ollama` is selected.
  `semantic-demo` prints `PARALLEL_OLLAMA_BATCHED=true` and
  `SEMANTIC_SELECT_STATUS=PASS`.
- Defer live-response-compression and broader CLI polish to v0.5.

## 0.3.0 - 2026-06-20

- Add opt-in `--allow-start` local stdio introspection for command-discovered
  MCP servers. The harness is timeout-bounded, byte-bounded, stdlib-only, and
  fixture-proven; it lists tools through MCP `tools/list` without becoming a
  proxy, gateway, service, browser UI, org scanner, or hosted dashboard.
- Extend `config-apply` with `--allow-start` and `--materialize-tools-list` so a
  previously `not_patchable` command-discovered server can be materialized to a
  local `toolsListPath` sidecar and then enforced on later static scans.
- Add `config-audit` and `config-audit-demo` for read-only MCP config secret
  hygiene. Reports include path/class/length/fingerprint metadata only and never
  print literal secret values.
- Add `config-multiserver-demo` and tests for explicit multi-server
  `{servers:[...]}` external catalog patching and fail-closed malformed-shape
  reporting.
- Keep the core package dependency-free and local-first.

## 0.2.0 - 2026-06-19

- Add `semantic-select` and `semantic-demo` for deterministic fixture-backed
  semantic selection, with optional Ollama embedding calls through stdlib HTTP.
- Add `compress-responses` and `compress-demo` for deterministic response
  fixture compression with before/after budget proof.
- Add `config-apply` and `config-demo` for dry-run-first selected-tool config
  patches with explicit write mode, per-file backups, and redacted reports.
  The apply contract is enforced end to end: it patches inline `tools` AND
  external `toolsListPath` files; binds the lock to the target config by
  `config_fingerprint`, refusing a foreign/stale lock unless
  `--allow-fingerprint-mismatch`; reports `PARTIAL` (never a false `PASS`) and
  lists `not_patchable` servers when a command-discovered server cannot be
  enforced; and the loader now honors `enabled: false`, so a disable actually
  drops the tool on the next scan.
- Keep the core package dependency-free and local-first.

## 0.1.0 - 2026-06-19

- Initial local-first MCP context budget scanner, selector, checker, exporter,
  and Docker demo.

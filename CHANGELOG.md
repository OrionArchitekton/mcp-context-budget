# Changelog

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

# Stdio Framing Compatibility Spec

## Scenario

`mcp-context-budget --allow-start` must introspect local stdio MCP servers that
use the current MCP SDK JSON-lines transport while preserving compatibility with
the legacy `Content-Length` fixture transport.

## Constraints

- Live startup remains opt-in behind `--allow-start`.
- Startup remains local-only, `shell=False`, timeout-bounded, and
  stdio-byte-bounded.
- Environment values remain redacted from reports and stderr pass-through.
- No hosted service, proxy, gateway, browser UI, org scanner, or long-running
  server is introduced.
- Static `toolsListPath` behavior remains unaffected.
- Selected-tool enforcement is not applied unless an explicit `config-apply
  --write` command is run by the caller.

## Acceptance Criteria

1. `scan --config --allow-start` can list tools from a JSON-lines stdio MCP
   server.
2. The live-stdio fixture covers JSON-lines, explicit `Content-Length`, and
   `auto` fallback behavior.
3. `scan`, `select`, `semantic-select`, and `config-apply` expose a bounded
   `--stdio-framing auto|json-lines|content-length` option.
4. A server config may force framing with `stdioFraming`, `stdio_framing`, or
   `stdio-framing`.
5. `config-apply --dry-run --allow-start --materialize-tools-list` can plan a
   materialized `toolsListPath` sidecar without writing it.
6. Existing tests continue to pass with no package dependency additions.

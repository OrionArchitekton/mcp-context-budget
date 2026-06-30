# AGENTS.md - mcp-context-budget

## Repo Role

`mcp-context-budget` is a local-first Python CLI for measuring MCP tool-surface
context cost, selecting lean task-relevant tool sets, enforcing schema and
response budgets, and applying selected-tool locks to caller-owned MCP configs
with explicit write gates.

## Boundaries

- Owns the Python package, CLI, tests, specs, scripts, Docker packaging, and
  repo docs.
- Does not own caller MCP configs except through explicit opt-in apply commands.
- Preserve dependency-free core behavior and dry-run-first config mutation.

## Authority Order

1. `/home/orion/src/orion-estate/platform/orion-estate-audit/AGENTS.md`
2. `README.md`
3. `specs/`, `docs/`, and tests
4. `pyproject.toml`, CLI source, and scripts

## Validation

```bash
python -m pytest
ruff check .
```

For docs-only changes, run `git diff --check` at minimum.

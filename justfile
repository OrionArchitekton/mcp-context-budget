set shell := ["bash", "-euo", "pipefail", "-c"]

# Run mcp-context-budget pytest suite
test:
    PYTHONPATH=. python3 -m pytest -q

# Run ruff static checks
lint:
    ruff check mcp_context_budget tests

# Full preflight check
check: lint test

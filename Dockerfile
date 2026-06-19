FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE /app/
COPY mcp_context_budget /app/mcp_context_budget
RUN pip install --no-cache-dir .

ENTRYPOINT ["mcp-context-budget"]

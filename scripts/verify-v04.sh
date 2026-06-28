#!/usr/bin/env bash
set -euo pipefail

SCRATCH="${SCRATCH:-/tmp/grok-goal-9ea10ed364b5/implementer}"
WT="${WT:-$HOME/.worktrees/mcp-ctx-budget-v04}"
REPO="${REPO:-$HOME/src/orion-estate/personal-brand/oss-projects/mcp-context-budget/mcp-context-budget-oss}"
MAP="${MAP:-$HOME/.orion/maps/mcp-context-budget-v04-MAP-20260628.md}"
RECON="${RECON:-$HOME/.orion/goal-prompts/mcp-context-budget-v04-20260628.recon.json}"

mkdir -p "$SCRATCH"
cd "$WT"

echo "=== verify-v04 $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# VP1 precondition
{
  echo "=== VP1 precondition ==="
  head -5 "$RECON"
  grep -E 'approval_state|TIGHT|parallel-ollama|CENTRAL_FORK' "$MAP" | head -8
} > "$SCRATCH/precondition.txt"

# VP2 preflight
{
  echo "=== VP2 preflight ==="
  git -C "$REPO" status --porcelain
  echo "worktree_branch=$(git branch --show-current)"
  echo "worktree_head=$(git rev-parse HEAD)"
  echo "origin_main=$(git rev-parse origin/main)"
  echo "git_email=$(git config user.email)"
} > "$SCRATCH/preflight.txt"

# VP3 build-changes
{
  echo "=== VP3 build-changes ==="
  git diff origin/main --stat
  git diff origin/main --name-only
  git diff origin/main -- mcp_context_budget/live_stdio.py | grep -E '^\+def _read_|^\+def _write_|^\-def _read_|^\-def _write_' || echo "framing_transport_edits=none"
} > "$SCRATCH/build-changes.txt"

# VP4 pytest
.venv/bin/python -m pytest -q --tb=no 2>&1 | tee "$SCRATCH/pytest-output.txt"

# VP5 docker
docker build -t mcp-context-budget:local . 2>&1 | tail -5 | tee "$SCRATCH/docker-build.txt"
docker run --rm mcp-context-budget:local allow-start-demo --stdio-framing auto --start-timeout-seconds 2 --max-stdio-bytes 65536 \
  2>&1 | tee "$SCRATCH/docker-allow.txt"
docker run --rm mcp-context-budget:local semantic-demo --task "diagnose bug report" --max-tools 3 --max-schema-tokens 3000 --embedding-backend fixture \
  2>&1 | tee "$SCRATCH/docker-semantic.txt"
docker run --rm mcp-context-budget:local prove-parallel-ollama-demo \
  2>&1 | tee "$SCRATCH/docker-parallel.txt"

# VP6 version-docs
{
  echo "=== VP6 version-docs ==="
  grep '^version' pyproject.toml
  grep '^dependencies' pyproject.toml
  head -15 CHANGELOG.md
} > "$SCRATCH/version-docs.txt"

# VP7 release handoff
{
  echo "=== VP7 release handoff ==="
  gh pr list --repo OrionArchitekton/mcp-context-budget --head "$(git branch --show-current)" --json url,state,headRefName,title
  git tag -l 'v0.4*' || true
} > "$SCRATCH/release-handoff.txt"

# VP8 final bundle
{
  echo "branch=$(git branch --show-current)"
  echo "version=$(grep '^version' pyproject.toml)"
  grep -E 'STDIO_FRAMING|LIVE_INTROSPECTION' "$SCRATCH/docker-allow.txt" || true
  grep -E 'SEMANTIC_SELECT|PARALLEL_OLLAMA' "$SCRATCH/docker-semantic.txt" || true
  grep -E 'PARALLEL_OLLAMA' "$SCRATCH/docker-parallel.txt" || true
  cat "$SCRATCH/release-handoff.txt"
} | tee "$SCRATCH/final-fresh-turn.txt"

git diff origin/main > "$SCRATCH/worktree.patch"
echo "VERIFY_DONE artifacts in $SCRATCH"
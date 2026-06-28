#!/usr/bin/env bash
set -euo pipefail

# Self-locating: default the worktree/repo to the checkout that contains this
# script so verify-v04.sh runs in any clone or CI, not just the author's machine.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"

SCRATCH="${SCRATCH:-$(mktemp -d -t verify-v04-XXXXXX)}"
WT="${WT:-$REPO_ROOT}"
REPO="${REPO:-$REPO_ROOT}"
# MAP/RECON are author-local release-planning artifacts. They are optional: when
# unset (or pointing at a missing file) the precondition block is skipped instead
# of failing, so the script never depends on a private path layout.
MAP="${MAP:-}"
RECON="${RECON:-}"

mkdir -p "$SCRATCH"
cd "$WT"

echo "=== verify-v04 $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# VP1 precondition (optional author-local planning artifacts)
{
  echo "=== VP1 precondition ==="
  if [[ -n "$RECON" && -f "$RECON" ]]; then
    head -5 "$RECON"
  else
    echo "recon=skipped (set RECON to an existing file to include)"
  fi
  if [[ -n "$MAP" && -f "$MAP" ]]; then
    grep -E 'approval_state|TIGHT|parallel-ollama|CENTRAL_FORK' "$MAP" | head -8
  else
    echo "map=skipped (set MAP to an existing file to include)"
  fi
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
# Prefer a local .venv when present, else fall back to PYTHON / python3 on PATH.
if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi
"$PYTHON" -m pytest -q --tb=no 2>&1 | tee "$SCRATCH/pytest-output.txt"

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
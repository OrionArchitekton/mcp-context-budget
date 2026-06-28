---
title: mcp-context-budget — cut a PyPI + GHCR release
verified: 2026-06-28
review_after: 2026-12-28
topics: [release, pypi, ghcr, trusted-publishing, ci]
references:
  - .github/workflows/release.yml
  - pyproject.toml
  - CHANGELOG.md
---

# Releasing mcp-context-budget

Releases are cut by **pushing a `v*` git tag**. The `release` workflow
(`.github/workflows/release.yml`) then:

1. Builds the sdist + wheel and publishes to **PyPI via Trusted Publishing**
   (OIDC — no stored token/secret; mirrors the sibling `schemafit` repo).
2. Builds and pushes a Docker image to **GHCR** at
   `ghcr.io/orionarchitekton/mcp-context-budget`.

## One-time setup (PyPI Trusted Publisher) — REQUIRED before the first release

The PyPI publish step fails until a trusted publisher exists for the project.
Because the project does not yet exist on PyPI, register a **pending** publisher:

1. Log in to <https://pypi.org> → *Your projects* → *Publishing* → *Add a pending publisher*.
2. Fill in exactly:
   - **PyPI Project Name:** `mcp-context-budget`
   - **Owner:** `OrionArchitekton`
   - **Repository name:** `mcp-context-budget`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
3. Save. The first successful workflow run creates the project and converts the
   pending publisher into a normal one.

(Optional hardening: create a GitHub Environment named `pypi` in repo
Settings → Environments with required reviewers; the workflow already targets
`environment: pypi`.)

## Cut a release

1. Land the release commit on `main` with `pyproject.toml [project].version` and
   the `CHANGELOG.md` top entry both set to the new version (e.g. `0.4.1`).
2. Create the GitHub release + tag:
   ```bash
   gh release create vX.Y.Z -R OrionArchitekton/mcp-context-budget \
     --target main --title "mcp-context-budget vX.Y.Z" --notes-file <notes.md>
   ```
   (The tag push is what triggers the workflow.)

## Monitor

```bash
gh run list -R OrionArchitekton/mcp-context-budget --workflow release.yml --limit 3
gh run watch -R OrionArchitekton/mcp-context-budget <run-id>
```

## Validate (independent)

```bash
# PyPI artifact exists (wheel + sdist) and installs clean:
curl -s https://pypi.org/pypi/mcp-context-budget/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"
uv venv /tmp/verify-mcb -q && uv pip install --python /tmp/verify-mcb/bin/python "mcp-context-budget==X.Y.Z" -q
/tmp/verify-mcb/bin/mcp-context-budget --version   # expect: mcp-context-budget X.Y.Z
# GHCR image:
docker pull ghcr.io/orionarchitekton/mcp-context-budget:X.Y.Z
```

## Rollback

- PyPI is append-only: you cannot overwrite a version. **Yank** a bad release
  (`pip` won't select a yanked version) via the PyPI project page, then publish a
  fixed patch version. Never reuse a version number.
- GHCR: delete or re-tag the bad image; consumers pinning a digest are unaffected.

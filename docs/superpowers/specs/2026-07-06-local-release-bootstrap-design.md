# Local Release and Bootstrap Design

## Goal

Make Gaius usable end to end on the two first supported surfaces: Codex on this Linux workstation and Openclaw on the Mac host.

## Scope

This release is an alpha local editable release. It does not introduce package publishing, hosted infrastructure, automatic transcript import, or remote/mobile access. The work is limited to:

- Updating both hosts to the merged `main` code after PR #3.
- Making both `gaius` and `gaius-mcp` resolve to the current editable install.
- Pointing each host at its real local memory store.
- Running CLI, MCP, index, and doctor smoke checks.
- Documenting the repeatable process for the next local release.

## Target Surfaces

Codex runs on this Linux workstation with:

- Repo: `/home/lane/wip/gaius`
- Install: `/home/lane/wip/gaius/.venv`
- Binaries: `/home/lane/.local/bin/gaius` and `/home/lane/.local/bin/gaius-mcp`
- Config: `/home/lane/.config/gaius/config.toml`
- Store: `/home/lane/memory`
- Codex MCP config: `/home/lane/.codex/config.toml`, `mcp_servers.gaius`

Openclaw runs on the Mac host reachable at `codex@macbook-pro.tailb2ed54.ts.net` with:

- Repo: `/Users/codex/gaius`
- Install: `/Users/codex/gaius/.venv`
- Binary: `/Users/codex/.local/bin/gaius-mcp`
- Config: `/Users/codex/.config/gaius/config.toml`
- Store: `/Users/codex/memory`

## Release Process

The release process is intentionally simple:

1. Update each host's Gaius checkout to `origin/main`.
2. Create or reuse a Python virtualenv.
3. Install Gaius editable with development dependencies: `pip install -e ".[dev]"`.
4. Link `gaius` and `gaius-mcp` into `~/.local/bin` where needed.
5. Verify the installed entry points run from a non-interactive shell.
6. Run `python -m pytest tests/ -q` in each checkout.

This keeps the code path transparent and easy to patch while the project is alpha. Wheel builds, version tags, changelog automation, and package registry release are deferred until Gaius has a stable operator workflow.

## Bootstrap Process

Each host gets a local memory store. The store remains plain files plus a derived `.gaius/index.db`.

On Linux, replace the stale setup-test config with:

```toml
store_path = "/home/lane/memory"
```

On Mac, keep:

```toml
store_path = "/Users/codex/memory"
```

For each store:

1. Ensure the store layout exists with `global/`, `projects/`, `.gitignore`, and `.gitattributes`.
2. Ensure the store is a git repo with an initial commit if it has no commits.
3. Run `gaius index --rebuild`.
4. Run `gaius doctor`.

No sync remote is required for this first E2E bootstrap. If a remote already exists, the process must not overwrite it.

## MCP Verification

Codex already has a `gaius` MCP entry. The bootstrap should verify it points at the current local venv:

```toml
[mcp_servers.gaius]
command = "/home/lane/wip/gaius/.venv/bin/gaius-mcp"
```

Openclaw's MCP configuration location must be discovered on the Mac host before editing. If no active config is found, document the exact binary path and leave a clear manual step rather than guessing an Openclaw-specific config shape.

MCP smoke verification should use the server directly enough to prove startup and tool registration. A full conversational Openclaw session is out of scope unless the installed Openclaw CLI exposes a reliable non-interactive MCP check.

## E2E Acceptance Checks

The work is complete when:

- Linux `gaius doctor` reports `/home/lane/memory`.
- Mac `gaius doctor` reports `/Users/codex/memory`.
- Both hosts pass the Gaius test suite.
- Both hosts can rebuild the local index.
- Linux Codex config points at the current `gaius-mcp`.
- Openclaw host has a verified `gaius-mcp` binary path and either confirmed config or documented next manual registration step.
- The repo contains a repeatable release/bootstrap document or script.

## Constraints

- Preserve self-sovereign defaults. Do not configure hosted embeddings or third-party storage.
- Do not replace or delete existing memory-store contents.
- Do not overwrite unrelated user config.
- Do not add broad automation that assumes future release policy.

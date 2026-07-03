# Gaius

Gaius is a file-first memory system for AI agents: one canonical store of markdown files (plus binary artifacts) in a git repo, shared by every agent and tool — Claude Code, Codex CLI, Openclaw, GUI apps — through two doors: the `gaius` CLI on machines with a shell, and the `gaius-mcp` MCP server everywhere else. A rebuildable SQLite index (FTS5 + optional vectors) at `<store>/.gaius/index.db` provides hybrid search; it is derived state and can always be deleted and rebuilt.

Full design: [docs/PRD.md](docs/PRD.md).

## Install

Requires Python ≥ 3.11 (3.14 recommended, e.g. via mise).

```bash
git clone git@github.com:clawfordlabs/gaius.git && cd gaius
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional extras: `pip install -e ".[fastembed]"` for local embeddings, `".[dev]"` for pytest.

## Setup

### 1. Create the memory store

```bash
gaius init ~/memory
```

This creates the store layout (`global/`, `projects/`, `vaults/`), a git repo, and writes `~/.config/gaius/config.toml` pointing at it.

### 2. Wire in existing vaults (optional)

Point the indexer at existing PKM vaults (Obsidian, Logseq) by listing them in `~/.config/gaius/config.toml`. They are indexed read-only; Gaius never writes into them.

```toml
store_path = "/home/lane/memory"
vault_paths = [
  "~/Documents/Obsidian Vault",
  "~/vaults/logseq-lane",       # e.g. a clone of a Logseq graph repo
]
```

Then build the index and check status:

```bash
gaius index --rebuild
gaius doctor
```

### 3. Choose an embedder (optional)

Search works out of the box in FTS5-only mode. For semantic search, either install fastembed (local, no API key) or configure an OpenAI-compatible endpoint:

```toml
embedder = "openai"          # or "fastembed", "auto" (fastembed if present), "none"

[openai]
base_url = "https://api.openai.com/v1"
key_env = "OPENAI_API_KEY"   # name of the env var holding the key
model = "text-embedding-3-small"
```

Vectors go into a sqlite-vec table when the extension loads, with an automatic pure-Python fallback; `gaius doctor` reports which path is active.

### 4. Register the MCP server

Claude Code:

```bash
claude mcp add gaius -- /path/to/gaius/.venv/bin/gaius-mcp
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.gaius]
command = "/path/to/gaius/.venv/bin/gaius-mcp"
```

Openclaw and anything else that speaks MCP over stdio: point it at the same `gaius-mcp` binary. The server exposes `search_memory`, `add_memory`, `get_project_state`, `handoff`, `log_decision`, `list_projects`, and `read_doc`.

### 5. Give agents standing instructions

```bash
gaius stub claude >> ~/.claude/CLAUDE.md
gaius stub agents >> ~/AGENTS.md
```

The stub tells agents to read `projects/<name>/STATE.md` at session start, use `gaius search` for recall, record decisions with `gaius decide`, and run `gaius handoff` before ending substantial work.

### 6. Sync between machines

`gaius sync` wraps pull → commit → push against whatever remote the store repo has — typically a bare repo on an always-on node reached over a tailnet, never a hosting service holding plaintext:

```bash
git -C ~/memory remote add origin user@node:/srv/memory.git
gaius sync
```

On a merge conflict it stops loudly with instructions rather than leaving the repo mid-merge.

## Everyday use

```bash
gaius add "Restic to B2 needs B2_ACCOUNT_ID/B2_ACCOUNT_KEY." --tags tooling,backup --topic tooling
gaius search "B2 backup credentials"
gaius state myproject            # read the handoff file + recent decisions
gaius handoff myproject -m "Shipped phase 1; next: deploy to the node."
gaius decide myproject "Use markdown files as the canonical store."
gaius projects                   # list projects, most recently touched first
gaius doctor                     # store, git, index freshness, embedder status
```

`GAIUS_MEMORY_DIR=/tmp/scratch` points any command at an alternate store (used by the tests, which never touch a real store).

Search notes: query text is sanitized before it reaches FTS5 — each term is quoted as a phrase and terms are joined with `OR` for broad recall — so quotes, boolean operators, parentheses, and `*` in a query are safe.

---

Copyright © 2026 The Clawford Company. All rights reserved.

# Gaius

Gaius is a file-first memory system for AI agents: one canonical store of markdown files (plus binary artifacts) in a git repo, shared by every agent and tool — Claude Code, Codex CLI, Openclaw, GUI apps — through two doors: the `gaius` CLI on machines with a shell, and the `gaius-mcp` MCP server everywhere else. A rebuildable SQLite index (FTS5 + optional vectors) at `<store>/.gaius/index.db` provides hybrid search; it is derived state and can always be deleted and rebuilt.

Full design: [docs/PRD.md](docs/PRD.md). Landscape: [docs/competitors.md](docs/competitors.md).

## Privacy: self-sovereign by default

By default, **no data ever leaves your machines and no third-party provider is involved** — this is a design principle, not a current limitation, and it is the main differentiator versus hosted memory products:

- The canonical store is markdown files in a local git repo you own.
- The search index is a local SQLite file, derived from the store and disposable.
- Search works fully locally: FTS5 keyword search out of the box, plus local embeddings (fastembed) if installed. Nothing is sent anywhere to index or query your notes.
- Sync (when configured) is git over SSH between your own machines — a bare repo on a box you own, typically over a tailnet. No hosting service holds plaintext.
- Offsite backup is your responsibility and should be client-side encrypted (e.g. restic), so cloud storage only ever holds ciphertext.

The one way data can leave: explicitly configuring `embedder = "openai"` with a hosted endpoint, which sends the text of every indexed chunk to that provider. Treat that as what it is — opting out of self-sovereignty — or point the same setting at a local server (e.g. Ollama) and keep the guarantee.

Why local-only is good enough: Gaius runs hybrid search (BM25 keyword matching fused with vectors), and on a personal corpus — thousands of documents, not millions, where queries usually share vocabulary with what you wrote — BM25 carries much of the load and mid-tier local embedding models close most of the remaining gap. Hosted embeddings earn their keep on massive or multilingual corpora; a personal memory store is neither. And the index is rebuildable in minutes, so if a stronger local model ships tomorrow, switching is one config line plus `gaius index --rebuild` — never a data migration.

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

This creates the store layout (`global/`, `projects/`), a git repo, and writes `~/.config/gaius/config.toml` pointing at it.

### 2. Wire in existing files (optional)

Point the indexer at existing files by adding `[[external]]` roots in `~/.config/gaius/config.toml`. They are indexed in place, never copied into the store, and Gaius never writes into an external root.

```toml
store_path = "/home/lane/memory"

[[external]]
path = "~/Documents/Obsidian Vault"

[[external]]
path = "~/wip/khorus"
project = "khorus"
exclude = ["node_modules/**"]
```

Defaults: external indexing includes `.md`, `.markdown`, `.txt`, and `.pdf`; respects `.gitignore` when the external root is a git repo; skips files over 10 MB; and supports optional `include`/`exclude` globs per root. Search indexes are per-host: store results print store-relative paths, while external results print absolute paths valid on that host. The old `vault_paths = [...]` config key is still read for backward compatibility and treated as external roots without project attribution.

Then build the index and check status:

```bash
gaius index --rebuild
gaius doctor
```

### 3. Choose an embedder (optional)

Search works out of the box in FTS5-only mode. For semantic search, either install fastembed (local, no API key) or configure an OpenAI-compatible endpoint:

```toml
embedder = "fastembed"       # or "auto" (fastembed if present), "openai", "none"

[fastembed]
model = "BAAI/bge-small-en-v1.5"  # optional; default fastembed model is unchanged when omitted

# Or use a hosted/local OpenAI-compatible endpoint with: embedder = "openai"

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

Openclaw and anything else that speaks MCP over stdio: point it at the same `gaius-mcp` binary. The server exposes `search_memory`, `add_memory`, `get_project_state`, `handoff`, `log_decision`, `list_projects`, `read_doc`, `run_task`, and `task_status`.

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

### 7. Delegate tasks to an agent

`gaius task <project> "<prompt>"` starts a background agent CLI in `projects/<project>`. Configure the executor in `~/.config/gaius/config.toml`; Gaius appends the prompt as the final argument.

Codex example:

```toml
task_command = ["codex", "exec", "--sandbox", "workspace-write"]
```

Claude example:

```toml
task_command = ["claude", "-p"]
```

This is the escape hatch for file-heavy work that does not fit the narrow memory tools: transcribe audio artifacts, OCR scanned PDFs, re-analyze workout exports, or inspect project files with a full agent. Logs go to `.gaius/tasks/<task-id>.log`; completed output is written to `projects/<project>/notes/tasks/<task-id>.md` and indexed.

```bash
gaius task myproject "OCR the scanned PDFs in artifacts/"
gaius task --status <task-id>
gaius task --list
```

## Everyday use

```bash
gaius add "Restic to B2 needs B2_ACCOUNT_ID/B2_ACCOUNT_KEY." --tags tooling,backup --topic tooling
gaius search "B2 backup credentials"
gaius state myproject            # read the handoff file + recent decisions
gaius handoff myproject -m "Shipped phase 1; next: deploy to the node."
gaius decide myproject "Use markdown files as the canonical store."
gaius projects                   # list projects, most recently touched first
gaius task myproject "Summarize the PDF artifacts."
gaius doctor                     # store, git, index freshness, embedder status
```

`GAIUS_MEMORY_DIR=/tmp/scratch` points any command at an alternate store (used by the tests, which never touch a real store).

Search notes: query text is sanitized before it reaches FTS5 — each term is quoted as a phrase and terms are joined with `OR` for broad recall — so quotes, boolean operators, parentheses, and `*` in a query are safe.

**IMPORTANT NOTE:** This code is unaudited and largely untested. It should be considered an alpha product. It's not intended for production-grade work.

---

Copyright © 2026 The Clawford Company. All rights reserved.

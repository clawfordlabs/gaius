# Gaius

Gaius is a file-first memory system for AI agents. The canonical store is a git repo of markdown files, with a rebuildable SQLite FTS index at `.gaius/index.db`.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`fastembed` is optional:

```bash
pip install -e ".[fastembed]"
```

## Quickstart

```bash
gaius init ~/memory
gaius add "Restic to B2 needs B2_ACCOUNT_ID/B2_ACCOUNT_KEY." --tags tooling,backup --topic tooling
gaius search "B2_ACCOUNT_KEY"
gaius handoff gaius --message "Current status and next steps."
gaius decide gaius "Use markdown files as the canonical store."
gaius sync
```

Use `GAIUS_MEMORY_DIR=/tmp/gaius-memory` to point commands at a temporary store.

Search text is sanitized before it reaches SQLite FTS5: Gaius tokenizes the query, quotes each term as a phrase, and joins terms with `OR` for broad recall. This prevents user input such as quotes, boolean operators, parentheses, or `*` from being interpreted as raw FTS5 syntax.

## MCP

Claude Code:

```bash
claude mcp add gaius -- gaius-mcp
```

Codex `mcp_servers` entry:

```toml
[mcp_servers.gaius]
command = "gaius-mcp"
```

The MCP server exposes `search_memory`, `add_memory`, `get_project_state`, `handoff`, `log_decision`, `list_projects`, and `read_doc`.

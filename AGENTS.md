# Agent instructions for the Gaius repo

Read `docs/PRD.md` before making design decisions; `docs/competitors.md` explains positioning.

## Two rules that are never broken

1. **Self-sovereign by default.** No feature may send user data off the user's machines by default. Anything that talks to a third-party service must be explicit opt-in configuration, clearly documented as such in the README. This is the product's main differentiator — do not trade it for convenience.
2. **Boundary rule.** Gaius never talks to a model and never makes a decision — no model loops, no agent runtime, no scheduling, no channels. The only exception is the optional, user-configured embeddings call. Work that needs an LLM exits through `run_task` into an external harness. If a proposed feature needs a model to work, it belongs in a harness, not in Gaius.

## Working in this repo

- Python ≥ 3.11, src/ layout, setuptools. Dev setup: `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`.
- Run tests with `python -m pytest tests/ -q`. All tests must pass on Linux and macOS; keep the code free of platform-specific dependencies (note: macOS has no `timeout` command).
- Tests must never touch a real memory store — always point `GAIUS_MEMORY_DIR` at a temp dir.
- Keep dependencies minimal (click, mcp, pypdf, sqlite-vec); anything heavier is an optional extra. Everything must degrade gracefully: no embedder, no git remote, no task_command, empty store.
- The index is derived state — any schema change must remain rebuildable from files via `gaius index --rebuild`. Files are canonical; never make the database the source of truth.
- Work on a feature branch, never commit directly to main.

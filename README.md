# Gaius

Gaius is a file-first memory system for AI agents: one canonical store of
markdown files (plus binary artifacts) in a git repo, shared by every agent and
tool through two doors. Machines with a shell use the `gaius` CLI; GUI apps and
other MCP-capable surfaces use the `gaius-mcp` server. A rebuildable SQLite
index at `<store>/.gaius/index.db` provides FTS5 search plus optional vectors;
it is derived state and can always be deleted and rebuilt.

Full design: [docs/PRD.md](docs/PRD.md). Landscape: [docs/competitors.md](docs/competitors.md).

## Privacy: self-sovereign by default

By default, **no data ever leaves your machines and no third-party provider is
involved**. This is a design principle, not a current limitation, and it is the
main differentiator versus hosted memory products:

- The canonical store is markdown files in a local git repo you own.
- The search index is a local SQLite file, derived from the store and disposable.
- Search works fully locally: FTS5 keyword search out of the box, plus local
  embeddings (fastembed) if installed. Nothing is sent anywhere to index or
  query your notes.
- Sync is git over SSH between your own machines: a bare repo on a box you own,
  typically over a tailnet. No hosting service holds plaintext.
- Offsite backup is your responsibility and should be client-side encrypted
  (e.g. restic), so cloud storage only ever holds ciphertext.

The one way data can leave: explicitly configuring `embedder = "openai"` with a
hosted endpoint, which sends the text of every indexed chunk to that provider.
Treat that as what it is, opting out of self-sovereignty, or point the same
setting at a local server (e.g. Ollama) and keep the guarantee.

Why local-only is good enough: Gaius runs hybrid search, with BM25 keyword
matching fused with vectors. On a personal corpus, where queries usually share
vocabulary with what you wrote, BM25 carries much of the load and mid-tier local
embedding models close most of the remaining gap. Hosted embeddings earn their
keep on massive or multilingual corpora; a personal memory store is neither.
The index is rebuildable in minutes, so if a stronger local model ships
tomorrow, switching is one config line plus `gaius index --rebuild`, never a
data migration.

## Install With An Agent

The fastest way to wire Gaius into a tool is to give that tool one setup
instruction and let it do the rest. The agent should clone the repo, run
`./setup`, read the generated instructions, and install whichever MCP and skill
integration matches its own harness.

Paste this into Claude Code, Codex, Hermes, OpenClaw, or another shell-capable
agent:

```markdown
Install Gaius for this agent.

Run:

git clone git@github.com:clawfordlabs/gaius.git ~/gaius && ~/gaius/setup

If SSH access to GitHub is not configured, use:

git clone https://github.com/clawfordlabs/gaius.git ~/gaius && ~/gaius/setup

Then read the setup output and finish the integration for this harness:

1. Register `~/gaius/.venv/bin/gaius-mcp` as an MCP server named `gaius`.
2. Install the generated skill from `~/gaius/.venv/bin/gaius stub skill`
   into this harness's user/global skills directory if it has one.
3. Add the lightweight standing-instructions stub from `gaius stub agents` or
   `gaius stub claude` only if this harness uses those files.
4. Run `gaius doctor` and tell me whether the memory store, git remote, index,
   MCP registration, and skill installation look correct.
5. Ask me whether I want this memory store synced with another machine I own.
```

## Install

Requires Python ≥ 3.11 (3.14 recommended, e.g. via mise). The steps below are
what `./setup` automates for the venv/install/store portion, plus manual detail
on the MCP/skill registration `./setup` prints commands for.

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

This creates the store layout (`global/`, `projects/`), a git repo, and writes
`~/.config/gaius/config.toml` pointing at it.

### 2. Wire in existing files (optional)

Point the indexer at existing files by adding `[[external]]` roots in
`~/.config/gaius/config.toml`. They are indexed in place, never copied into the store, and
Gaius never writes into an external root.

```toml
store_path = "~/memory"

[[external]]
path = "~/Documents/Obsidian Vault"

[[external]]
path = "~/work/client-portal"
project = "client-portal"
exclude = ["node_modules/**"]
```

Defaults: external indexing includes `.md`, `.markdown`, `.txt`, and `.pdf`; respects
`.gitignore` when the external root is a git repo; skips files over 10 MB; and supports
optional `include`/`exclude` globs per root. Search indexes are per-host: store results
print store-relative paths, while external results print absolute paths valid on that host.
The old `vault_paths = [...]` config key is still read for backward compatibility and
treated as external roots without project attribution.

Then build the index and check status:

```bash
gaius index --rebuild
gaius doctor
```

### 3. Choose an embedder (optional)

Search works out of the box in FTS5-only mode. For semantic search, either
install fastembed (local, no API key) or configure an OpenAI-compatible
endpoint:

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

Vectors go into a sqlite-vec table when the extension loads, with an automatic
pure-Python fallback; `gaius doctor` reports which path is active.

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

Codex may expose MCP tools lazily. If a broad tool search shows only some Gaius
tools, search for the exact missing names before falling back to the CLI. For
example: `list_projects read_doc task_status gaius`.

Openclaw and anything else that speaks MCP over stdio: point it at the same
`gaius-mcp` binary. The server exposes `search_memory`, `add_memory`,
`get_project_state`, `handoff`, `log_decision`, `list_projects`, `read_doc`,
`sync`, `run_task`, and `task_status`.

### 5. Teach agents to use gaius

MCP tools are self-documenting — an agent that sees `search_memory`, `add_memory`, etc. in
its tool list generally figures out when to call them. The `gaius` CLI is not
self-documenting: an agent only knows to run `gaius search "..."` if something tells it
to. Two mechanisms cover this, and which ones apply depends on whether the tool has a
skills system.

**Skills (preferred, where supported).** A skill is a small markdown file (YAML
frontmatter + instructions) that a tool loads on demand, triggered by matching the
`description` field against what the user asked for — so "remember that X" or "hand off
this project" resolves to the right `gaius` command without the agent needing an
always-loaded reminder. Claude Code, the Hermes agent, and OpenClaw all support installing
a skill from a local directory or file, using a converging "agent skills" format:

```bash
gaius stub skill > /tmp/gaius-SKILL.md
```

- **Claude Code**: skills live at `~/.claude/skills/<name>/SKILL.md` (personal) or
  `.claude/skills/<name>/SKILL.md` (project). Create the directory and copy the file in:
  ```bash
  mkdir -p ~/.claude/skills/gaius && gaius stub skill > ~/.claude/skills/gaius/SKILL.md
  ```
- **Hermes**: `hermes skills install <path-or-url>` (accepts a local `SKILL.md` path).
- **OpenClaw**: `openclaw skills install <local-dir> --global` (accepts a local
  directory containing `SKILL.md`), then `openclaw mcp reload` isn't needed for skills,
  but a running agent picks up new skills on its next turn.

**Standing instructions (fallback, and useful everywhere).** Some harnesses also
read always-loaded instruction files such as `AGENTS.md` or `CLAUDE.md`. For
those, append a short always-loaded pointer too:

```bash
gaius stub claude >> ~/.claude/CLAUDE.md
gaius stub agents >> ~/AGENTS.md
```

The stub tells agents to run `gaius sync` before shared reads, read
`projects/<name>/STATE.md` at session start, use `gaius search` for recall,
record decisions with `gaius decide`, run `gaius handoff` before ending
substantial work, and run `gaius sync` again after writes. It is worth keeping
even on skill-capable tools as a lightweight backstop. Skills are triggered by
the model's own judgment and can occasionally be missed; an always-loaded stub
cannot.

Not yet explored: hooks (tool-level lifecycle triggers, e.g. auto-running `gaius handoff` on
session end) as a third, even-less-optional mechanism on tools that support them.

### 6. Sync between machines

`gaius sync` commits local writes, merges remote changes without rebasing, then
pushes against whatever remote the store repo has. This is typically a bare repo on an
always-on node reached over a tailnet, never a hosting service holding plaintext:

```bash
git -C ~/memory remote add origin user@node:/srv/memory.git
gaius sync
```

Concurrent, complete timestamped handoffs and decision entries written by Gaius are
merged automatically in chronological order only when the file exists in the common Git
base and both sides preserve every base entry unchanged. The semantic resolver rejects
duplicate timestamps when resolving a Git conflict; clean Git merges bypass that resolver.
Concurrent first creation of the same project and every other unsafe conflict stop loudly
and intentionally remain in Git's protected merge state. Resolve and commit the files
manually, or run `git merge --abort`; `gaius sync` refuses retries until the operation is
complete.

Handoff summaries may use any Markdown heading. Gaius uses
`<!-- gaius-handoff-end -->` as an internal record boundary; a summary cannot contain
that marker.

Every agent should sync before its first shared-memory read and immediately after every
Gaius write. MCP-only agents call the `sync` tool after `add_memory`, `handoff`,
`log_decision`, or a completed `run_task` whose result should be visible elsewhere. A
successful MCP sync returns `ok: true`; `clean: true` means there are no remaining
uncommitted memory changes.

### 7. Delegate tasks to an agent

`gaius task <project> "<prompt>"` starts a background agent CLI in
`projects/<project>`. Configure the executor in `~/.config/gaius/config.toml`;
Gaius appends the prompt as the final argument.

Codex example:

```toml
task_command = ["codex", "exec", "--sandbox", "workspace-write"]
```

Claude example:

```toml
task_command = ["claude", "-p"]
```

This is the escape hatch for file-heavy work that does not fit the narrow memory
tools: transcribe audio artifacts, OCR scanned PDFs, re-analyze workout exports,
or inspect project files with a full agent. Logs go to
`.gaius/tasks/<task-id>.log`; completed output is written to
`projects/<project>/notes/tasks/<task-id>.md` and indexed.

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

`gaius decide` records one line per decision. Put longer rationale in a note or
handoff, then record the concise decision here.

`GAIUS_MEMORY_DIR=/tmp/scratch` points any command at an alternate store. The
tests use this so they never touch a real store.

Search notes: query text is sanitized before it reaches FTS5. Each term is
quoted as a phrase and terms are joined with `OR` for broad recall, so quotes,
boolean operators, parentheses, and `*` in a query are safe.

**IMPORTANT NOTE:** This code is intended for self-hosted personal memory
workflows. Review the privacy model and configuration before using it with
sensitive data.

---

Copyright (c) 2026 Clawford Labs. Released under the MIT License.

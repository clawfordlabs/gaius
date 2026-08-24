# Gaius — Universal Agent Memory

**Status:** v1 spec · **Date:** 2026-07-03 · **Owner:** Clawford Labs

## 1. Problem

AI tool memory is fragmented in two directions:

- **Within tools:** each project folder has its own CLAUDE.md / AGENTS.md / local memory. Knowledge earned in one project is invisible to the others.
- **Among tools:** Claude Code, Codex CLI, Openclaw, Gemini, and the GUI apps each keep private memory. None can read the others'. Openclaw's memory is decent but locked to Openclaw.

Separately, years of personal knowledge (Obsidian, Logseq — hundreds of markdown files) sit entirely outside all AI workflows.

Two concrete needs:

1. **Universal memory** — facts, preferences, lessons — readable and writable from every tool, with embedding-based fuzzy search.
2. **Project memory** — durable per-project state and artifacts, so a session started in Claude Code on the desktop can be picked up later by Openclaw (or any other agent) exactly where it left off.

Prior art rejected: gbrain (overengineered, cron-dependent, opaque failures, nothing for project handoff), Honcho/mem0 (conversation-derived user modeling only, hosted, nothing for artifacts or handoff).

## 2. Design thesis

- **Plain files in one git repo are the canonical store.** Every tool that matters can already read and write files. Markdown for memories and state; binaries allowed as artifacts.
- **Everything else is a derived view.** The embedding index is rebuildable from the files at any time and is never the source of truth. Delete it and nothing is lost.
- **Two access patterns, one store.** Recall (fuzzy: "what do I know about X?") uses embedding + full-text search. Handoff (exact: "where is this project?") uses fixed, well-known file locations — never fuzzy retrieval.
- **Two doors.** Machines with a shell use the filesystem + CLI directly. Everything else (GUI apps, mobile) goes through an MCP server that wraps the same operations on the same files.
- **Git is transport and history, not the data model.** Single branch, no rebasing, auto-commits, partitioned files so concurrent writers rarely collide. If git chafes later, the transport swaps out; files, CLI, MCP, and agent integrations are untouched.
- **No third party holds plaintext.** The canonical node is a machine the user controls, reached over a private network such as a tailnet. Offsite backup is client-side encrypted (restic). Memory-store sync does not require GitHub or any hosted plaintext service.

## 3. Goals / non-goals

**Goals (v1):**
- One memory store usable from Claude Code, Codex CLI, Openclaw, and the GUI apps on desktop.
- Hybrid search (vector + full-text) over memories, project notes, and external roots (Obsidian/Logseq vaults, existing project directories — see §5.1, §7).
- Fixed-location project state (`STATE.md`) and decision log (`DECISIONS.md`) with CLI/MCP helpers to read and update them.
- Text extraction from PDF artifacts at index time so binaries are searchable.
- One-command sync (`gaius sync`) that commits local writes, merges remote changes without rebasing, then pushes; agents never run raw git.
- Standing-instruction stubs for CLAUDE.md / AGENTS.md so agents actually use the store.

**Boundary rule (permanent non-goal):** Gaius never talks to a model and never makes a decision. No model loop, no agent runtime, no scheduling, no channels. The one exception is the optional embeddings call, and even that is config, not architecture. Anything that requires intelligence leaves Gaius through `run_task` and runs in a harness the user already operates (openclaw, Claude Code, Codex). This is the line that keeps Gaius a filing cabinet with a search index rather than another agent harness. If a proposed feature needs an LLM to work, it belongs in a harness, not here.

**Non-goals (v1):**
- Mobile / remote access (phase 3 — see §9).
- Deck/image/audio extraction beyond PDF text (later).
- Automatic memory capture from transcripts — meaning LLM-derived fact extraction. Writes are explicit, prompted by standing instructions. (Raw transcript *indexing* — no model, just text extraction — is planned for v2; see §9.)
- Multi-user anything.
- Replacing each tool's native memory. Gaius sits beside it; the stubs point tools at it.

## 4. Architecture

```
┌────────────── canonical node (user-controlled host) ──────────────────────┐
│  ~/memory (git repo, canonical)     .gaius/index.db (SQLite, derived)      │
│        ▲            ▲                     ▲                                │
│        │ files      │ files               │ read/write via core lib       │
│   gaius CLI    gaius-mcp server ◄── GUI apps (local MCP, stdio)           │
└──────────────▲─────────────────────────────────────────────────────────────┘
               │ git over tailnet SSH (bare repo on canonical node)
        laptop / other machines (same CLI, same layout)
```

One Python package, three entry points sharing one core library:
- `gaius` — CLI for shells and shell-capable agents.
- `gaius-mcp` — MCP server (stdio for v1) exposing the same operations.
- Indexer — invoked by `gaius index`, also run incrementally by write paths.

## 5. Memory repo layout

```
~/memory/
  global/                  # universal memory: one markdown file per fact/lesson
    health/  investing/  tooling/  ...        # topical dirs, freeform
  projects/
    <project>/
      STATE.md             # current status + next steps — THE handoff file
      DECISIONS.md         # append-only decision log
      notes/               # freeform durable notes
      artifacts/           # binaries: PDFs, decks, exports, raw data
  .gaius/                  # gitignored: index.db, locks, config overrides
  .gitignore  .gitattributes
```

**Memory file format** — markdown with minimal frontmatter:

```markdown
---
id: 2026-07-03-restic-b2-flags
tags: [tooling, backup]
source: claude-code            # which agent/tool wrote it
created: 2026-07-03
---
Restic to B2 needs B2_ACCOUNT_ID/B2_ACCOUNT_KEY env vars; repo URL format is b2:bucket:path.
```

Frontmatter is optional on read (vault files have their own conventions or none); required fields are generated automatically on `gaius add`.

**STATE.md convention** — free-form markdown but always answering: what is this project, current status, what's next, open blockers. `gaius handoff` prepends a timestamped session summary section.

### 5.1 What belongs in the store — and what stays outside

Existing projects fall into three populations, each with a different answer. The decision rule: *does the project need its own remote, CI, collaborators, or public visibility?*

- **Code projects with real repos** (own remotes, histories worth keeping, some public): stay exactly where they are. The memory store holds memory *about* a project — STATE.md, DECISIONS.md, notes, artifacts — never its working tree. Gaius's `projects/<name>/` is a sidecar; the link to the real repo is configuration, not containment: an external root (§7) attributed to the project makes the repo's documents searchable under `--project <name>`, and `run_task` can be pointed at the real working tree.
- **Document/life projects** (mostly PDFs and markdown, trivial or no git history, no remote): these files *are* memory. They move into `projects/<name>/artifacts/` wholesale, imported at current state. Old directories are kept as archives until confidence is earned; `git subtree add` is available if a small history genuinely matters, but at a handful of commits it usually doesn't.
- **Upstream clones and reference checkouts**: stay out entirely. Not our projects, not our memory.

**Never submodules.** Agents mishandle them, `gaius sync` would become an N-repo problem, and the content still wouldn't be in the store — all of the complexity, none of the benefit. Importing code histories via subtree is also rejected: it bloats the memory repo and breaks the GitHub story for public projects.

## 6. CLI spec

```
gaius init [path]                      # create/adopt a memory repo; write config
gaius add <text> [--tags a,b] [--topic health] [--project X]   # write a memory file + index it
gaius search <query> [--project X] [--limit N] [--full]        # hybrid search; prints snippets + paths
gaius show <id-or-path>                # print a memory/doc in full
gaius projects                         # list projects with last-touched times
gaius state <project>                  # print STATE.md (+ last N decisions)
gaius handoff <project> [--message|-m <summary>] [--stdin]     # prepend session summary to STATE.md
gaius decide <project> <text>          # append to DECISIONS.md
gaius index [--rebuild]                # incremental (or full) reindex, incl. vaults + PDF extraction
gaius sync [--message <msg>]           # git add/commit, pull --rebase=false, push; safe timestamped-entry merge, loud otherwise
gaius stub [claude|agents|generic]     # print standing-instruction block for CLAUDE.md/AGENTS.md
gaius doctor                           # config, index freshness, git remote reachability, embedder status
```

Config: `~/.config/gaius/config.toml` (store path, embedder choice, vault paths). Env override `GAIUS_MEMORY_DIR` for tests and odd setups.

## 7. Index & search

- **SQLite** at `<store>/.gaius/index.db`: `docs` table (path, title, mtime, hash, project, tags), `chunks` (doc, offset, text), FTS5 virtual table over chunks, and a vector table via **sqlite-vec**.
- **External roots:** `[[external]]` entries in config declare read-only directories *outside* the store that are indexed in place — PKM vaults (Obsidian, Logseq), the docs of a real code repo, any folder of files. Each entry has a `path`, an optional `project` (attributing results to a project, so `--project` filters cover both the sidecar and the repo's documents), and optional include/exclude globs. Defaults: extension allowlist (`.md`, `.txt`, `.pdf` — code is deliberately not indexed), `.gitignore` respected when the root is a git repo, per-file size cap. External files are never copied into the store; Gaius never writes into an external root. This generalizes and replaces the earlier `vault_paths` setting (and the `vaults/` symlink directory in the store layout).
- **Path semantics:** the index is per-host, derived, and never syncs; each host indexes the synced store plus whatever external roots *its own* config declares. Store-internal results are reported store-relative (the store may be mounted at different paths on different machines); external results are absolute paths valid on the answering host. A host only returns paths it indexed itself, so there are no dangling cross-host paths — external content is simply invisible from hosts that don't have it. In phase 3, the node answering a remote query is also the node serving the bytes, so its paths are the right ones.
- **Chunking:** by markdown heading, max ~400 tokens per chunk, with file path + heading as context prefix.
- **Embedders (pluggable):** `fastembed` (local ONNX, default if installed) → `openai`-compatible API (config: base URL, key env var, model) → `none`. With `none`, search is FTS5-only and everything still works. This is deliberate: the system must degrade gracefully, never break because a model or API is unavailable.
- **Hybrid search:** FTS5 (BM25) + vector cosine, merged with reciprocal rank fusion. `--full` prints whole files instead of snippets.
- **PDF extraction:** pypdf text → cached sidecar in the index (not a file in the repo); indexed like any doc, results point at the original artifact path.
- **Incremental:** mtime+hash based; `gaius add`/`handoff`/`decide` index their own writes immediately, so search is never stale for gaius-written content.

## 8. MCP server (v1: local stdio)

Tools (thin wrappers over the same core functions):

| tool | maps to |
|---|---|
| `search_memory(query, project?, limit?)` | `gaius search` |
| `add_memory(text, tags?, topic?, project?)` | `gaius add` |
| `get_project_state(project)` | `gaius state` |
| `handoff(project, summary)` | `gaius handoff` |
| `log_decision(project, text)` | `gaius decide` |
| `list_projects()` | `gaius projects` |
| `read_doc(path)` | `gaius show` |
| `sync(message?)` | `gaius sync` |
| `run_task(project, prompt)` | `gaius task` |
| `task_status(task_id)` | `gaius task --status` |

**`run_task` — the escape hatch.** The tools above are deliberately constrained (text-shaped questions and answers). `run_task` covers everything else: it hands an arbitrary prompt to a real agent process running on the node that has the files — transcribe the audio artifacts in this project, OCR these scanned PDFs, re-analyze the workout exports. The executor is configuration, not architecture: `task_command` in config.toml names the agent CLI to spawn (`codex exec`, `claude -p`, openclaw, …), run with the project directory as its working directory. Jobs run in the background and return a task id; `task_status` polls progress. Output is written to `projects/<name>/notes/tasks/<task-id>.md` and indexed, so results are immediately searchable. If `task_command` is unconfigured, `run_task` fails loudly with setup instructions. This is arbitrary code execution by design — it stays behind the same access boundary as the rest of Gaius (local machine now, authenticated endpoint in phase 3).

Registered per-tool: `claude mcp add gaius -- gaius-mcp`, Codex `mcp_servers` entry, Openclaw config. Same binary everywhere.

## 9. Sync & phasing

**Phase 1 (this build):** core lib, CLI, index/search, MCP stdio server, stubs, tests. Single machine.

**Phase 2:** bare repo on a user-controlled canonical node; `gaius sync` over private-network SSH; restic backup recipe (docs only — restic is configured by hand); second-machine onboarding docs. Gaius must run first-class on macOS and Linux: the full test suite and an MCP handshake are part of the deploy check.

**v2 — transcript indexing:** `gaius import-transcripts`, run per host, extracts the conversational text (user + assistant messages only — tool output and reasoning are skipped, which is where the bulk lives) from harness session logs into compact markdown digests at `transcripts/<host>/<harness>/<session>.md` in the store, where they sync and become searchable everywhere. Known sources: Claude Code (`~/.claude/projects/<path-slug>/*.jsonl` — project attribution from the directory slug), Codex CLI (`~/.codex/sessions/YYYY/MM/DD/*.jsonl` — attribution from `cwd` in session metadata), openclaw on the canonical node. Opt-in per harness: transcripts are the most sensitive data class the user has, and importing replicates digests to every synced machine. Boundary rule intact — this is raw text extraction, no model involved. **Scope honesty:** this does not enable mid-conversation resume across harnesses (no harness can load another's context); the ceiling is fresh session + `STATE.md` handoff + transcript search for what the handoff missed. Handoff stays the primary pickup mechanism; transcript search is the safety net that makes a lazy or forgotten handoff recoverable.

**Phase 3 (mobile/remote):** `gaius-mcp --http` behind auth (bearer token first, OAuth if claude.ai requires it), exposed via Tailscale Funnel. Signed short-lived URLs for downloading artifacts. `run_task` (already in §8) becomes the remote-work primitive: mobile never syncs files; it is a remote control for the node that has them.

## 10. Security posture

- Plaintext never leaves machines the user controls; sync is SSH inside a private network such as a tailnet.
- Offsite backup: restic (client-side encrypted) only.
- Phase 3 endpoint is a single authenticated door to a purpose-built server, not a file share; artifact URLs are short-lived and signed.
- Dependencies installed only through the sandboxed pip wrapper, inside a venv.

## 11. Risks

| risk | mitigation |
|---|---|
| Agents don't write memory (the real failure mode) | `gaius stub` standing instructions; handoff is one command; session-end hooks where the harness supports them |
| Git conflicts confuse agents | `gaius sync` commits before merge, safely orders complete timestamped handoff/decision additions, and stops loudly on every other conflict |
| Embedder unavailable (ONNX wheels, API keys) | FTS5-only mode is always functional; embedder is config, not architecture |
| Monorepo gets heavy | layout keeps projects self-contained; splitting a project out later is `git filter-repo` + a config edit |
| Index corruption/staleness | index is disposable; `gaius index --rebuild`; `gaius doctor` reports drift |

## 12. Open questions (deferred, not blocking)

- Deck (pptx/keynote), docx/rtf, and image extraction pipeline (a few docx/rtf files exist in real projects today; pdf+md+txt covers the large majority).
- Structured health-data ingestion (`query_health`-style tools) — likely a separate importer writing SQLite into `projects/health/artifacts/`.
- Whether phase-3 auth needs full OAuth for claude.ai remote connectors, or bearer suffices.
- Memory decay/dedup (merge near-duplicate facts) — revisit once volume warrants.

# Competitive landscape

**Date:** 2026-07-03. Survey of the agent-memory landscape and where Gaius fits. Companion to [PRD.md](PRD.md); see the PRD for the two problems Gaius solves (universal memory + project handoff).

## Summary

The market is crowded on one half of the problem (remember facts about the user) and empty on the other half (let any agent pick up a project where the last one left off). Nothing found in this survey does both, and nothing does either while keeping all data on machines the user owns by default. Gaius's differentiators are: plain markdown + git as the canonical store, project handoff as a first-class convention, delegation to existing harnesses instead of being one, and a default posture where no data ever leaves the user's machines.

## Categories

### 1. Memory-extraction platforms

**Mem0 / OpenMemory, Letta (MemGPT), Zep, Cognee, Honcho.** Where most of the funding and benchmarking activity is. Shared shape: a database is canonical, an extraction pipeline decides what to remember from conversations, retrieval is semantic.

- Model *users and conversations*, not projects with durable artifacts.
- No concept of session handoff between different tools/harnesses.
- The database is the source of truth — leaving means a data migration.
- Mostly hosted or hosted-first; Mem0's OpenMemory is the local-first exception and covers a real slice of the universal-memory problem via MCP, but still database-canonical.
- Letta is really an agent *runtime* (competes with harnesses, not with Gaius).

Verdict: solve the easier half of our problem at the cost of the data ownership that motivated the project. Not adopted.

### 2. File-first memory (the close neighbor)

**[basic-memory](https://github.com/basicmachines-co/basic-memory)** makes the same core bet as Gaius: markdown files as canon, Obsidian integration, MCP read/write, projects, a CLI. The closest existing product, and the one to keep watching. Differences that justified building anyway:

1. Its mobile/sync story is their hosted cloud holding your files in plaintext — exactly the constraint Gaius rejects. Gaius syncs over the user's own tailnet and never involves a hosting provider.
2. Its "projects" are knowledge-graph containers, not a working-session handoff ritual (STATE.md / DECISIONS.md / `gaius handoff`).
3. It carries its own machinery — sync daemon, knowledge-graph conventions, `memory://` URLs. Prior experience (gbrain) says opaque machinery is how these tools lose trust.
4. No delegated-task escape hatch.

Because both systems treat plain markdown as canonical, basic-memory can be pointed at the same store as Gaius and evaluated side by side at zero cost. If it ever covers enough, Gaius shrinks gracefully to the pieces it uniquely has.

### 3. Agent harnesses

**openclaw, Claude Code, Codex CLI, Letta-as-runtime.** Not competitors — they are the execution layer Gaius serves. The PRD's boundary rule keeps it that way: Gaius never talks to a model and never makes a decision; anything requiring intelligence exits through `run_task` into a harness the user already operates. `run_task` is ~250 lines of "spawn a process, remember its exit code, index its log" — a doorbell, not a brain.

### 4. Per-vendor native memory

Claude/ChatGPT/Gemini memory, CLAUDE.md/AGENTS.md conventions, openclaw's memory. Keeps improving and stays siloed — no vendor has an incentive to make its memory readable by a competitor's tool. The cross-vendor layer structurally has to come from the user's side, which is why this niche is filled by third parties rather than platform features.

### 5. Prior attempts (rejected before this survey)

- **gbrain** — theoretically covers a lot, in practice overengineered: too many features and cron jobs, hard to get working, opaque when it misbehaves, and nothing for project handoff.
- **Honcho** — conversation-derived user modeling behind someone else's API; nothing for artifacts or handoff.

## The exit-cost test

The evaluation lens that matters most: *what does leaving cost?* Leaving a Mem0/Letta/Zep-class product means migrating a database. Leaving basic-memory's cloud means repatriating files. Leaving Gaius means deleting ~1,700 lines of Python — the memory itself is markdown in a git repo the user owns, readable by whatever comes next. Gaius is built to be replaceable; the data layer is the investment, the code is disposable.

## Sources

- [Mem0 vs Letta comparison](https://vectorize.io/articles/mem0-vs-letta)
- [Mem0 vs Letta vs Zep vs Cognee (2026)](https://mcp.directory/blog/mem0-vs-letta-vs-zep-vs-cognee-2026)
- [State of AI agent memory 2026 (Mem0)](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Agent memory vendor landscape 2026](https://agentmarketcap.ai/blog/2026/04/10/agent-memory-vendor-landscape-2026-letta-zep-mem0-langmem)
- [basic-memory (GitHub)](https://github.com/basicmachines-co/basic-memory)
- [Unified agentic memory across harnesses using hooks](https://towardsdatascience.com/unified-agentic-memory-across-harnesses-using-hooks/)

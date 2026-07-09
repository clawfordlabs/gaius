from __future__ import annotations

import sys

import click

from .config import load_config
from .doctor import doctor_report
from .indexer import index_store, search
from .store import add_memory, decide as decide_memory, handoff as handoff_memory
from .store import init_store, list_projects, project_state, read_doc
from .sync import SyncError, sync as sync_memory
from .tasks import TaskError, list_tasks, run_task, task_status


def parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@click.group()
def cli() -> None:
    """Gaius file-first agent memory."""


@cli.command()
@click.argument("path", required=False)
def init(path: str | None) -> None:
    config = load_config(path)
    init_store(config, write_user_config=not bool(path is None and "GAIUS_MEMORY_DIR" in __import__("os").environ))
    index_store(config.store, config)
    click.echo(f"Initialized {config.store}")


@cli.command()
@click.argument("text")
@click.option("--tags", default=None)
@click.option("--topic", default=None)
@click.option("--project", default=None)
def add(text: str, tags: str | None, topic: str | None, project: str | None) -> None:
    config = load_config()
    path = add_memory(config, text, parse_tags(tags), topic, project)
    click.echo(path)


@cli.command("search")
@click.argument("query")
@click.option("--project", default=None)
@click.option("--limit", default=10, type=int)
@click.option("--full", "full_output", is_flag=True)
def search_cmd(query: str, project: str | None, limit: int, full_output: bool) -> None:
    config = load_config()
    results = search(config.store, config, query, project, limit)
    for result in results:
        click.echo(f"{result.path} :: {result.heading}")
        if full_output:
            click.echo(read_doc(config, str(result.path)).rstrip())
        else:
            click.echo(result.snippet)
        click.echo()

@cli.command()
@click.argument("id_or_path")
def show(id_or_path: str) -> None:
    click.echo(read_doc(load_config(), id_or_path).rstrip())


@cli.command()
def projects() -> None:
    for project, mtime in list_projects(load_config()):
        click.echo(f"{project}\t{mtime:.0f}")


@cli.command()
@click.argument("project")
def state(project: str) -> None:
    click.echo(project_state(load_config(), project).rstrip())


@cli.command()
@click.argument("project")
@click.option("--message", "-m", default=None)
@click.option("--stdin", "read_stdin", is_flag=True)
def handoff(project: str, message: str | None, read_stdin: bool) -> None:
    if read_stdin:
        summary = sys.stdin.read()
    elif message:
        summary = message
    else:
        raise click.UsageError("Provide --message or --stdin")
    path = handoff_memory(load_config(), project, summary)
    click.echo(path)


@cli.command()
@click.argument("project")
@click.argument("text")
def decide(project: str, text: str) -> None:
    path = decide_memory(load_config(), project, text)
    click.echo(path)


@cli.command("index")
@click.option("--rebuild", is_flag=True)
def index_cmd(rebuild: bool) -> None:
    config = load_config()
    count = index_store(config.store, config, rebuild)
    click.echo(f"Indexed {count} changed docs")


@cli.command()
@click.option("--message", default=None)
def sync(message: str | None) -> None:
    config = load_config()
    try:
        for line in sync_memory(config.store, message):
            click.echo(line)
    except SyncError as exc:
        raise click.ClickException(str(exc)) from exc


STUBS = {
    "claude": "CLAUDE.md",
    "agents": "AGENTS.md",
    "generic": "agent instructions",
}


@cli.command()
@click.argument("target", required=False, type=click.Choice(["claude", "agents", "generic", "skill"]))
def stub(target: str | None) -> None:
    target = target or "generic"
    if target == "skill":
        click.echo(SKILL_MD)
        return
    click.echo(
        f"""## Gaius Memory Instructions for {STUBS[target]}

- Read project state at session start with `gaius state <project>` or by opening `projects/<project>/STATE.md`.
- Run `gaius sync` before reading shared state so local memory is current.
- Use `gaius search "<query>"` before relying on memory or asking the user to repeat durable context.
- Add durable facts with `gaius add "<text>" --tags a,b --topic <topic>` or `--project <project>`.
- Log decisions with `gaius decide <project> "<decision>"`.
- Before ending work, run `gaius handoff <project> --message "<summary and next steps>"`.
- Run `gaius sync` immediately after writing memory. If sync fails, report the error instead of claiming other agents can see the update.
- Use `gaius sync` instead of raw git commands for the memory repo unless the user explicitly asks.
"""
    )


SKILL_MD = """---
name: gaius
description: Use whenever the user asks to remember, save, recall, or search durable facts and decisions ("remember that...", "what did we decide about...", "what do you know about..."); to check or hand off project state ("what's the state of X", "hand off this project", "log this decision"); or to delegate file-heavy work an agent can do unattended ("OCR these PDFs", "summarize these files"). If an MCP server named "gaius" is connected, prefer its tools (sync, search_memory, add_memory, get_project_state, handoff, log_decision, list_projects, read_doc, run_task, task_status) over the CLI below — same behavior, no shell-out needed. Fall back to the `gaius` CLI when no MCP connection is available.
---

# Gaius: file-first agent memory

Gaius is a shared, local-first memory store: markdown files in a git repo, indexed for
hybrid (keyword + optional vector) search. It is used by every agent and tool on this
machine and others on the same sync network — memory saved here is durable across
sessions and shared across tools, not private to one conversation.

`GAIUS_MEMORY_DIR` overrides the store location for a single command if ever needed;
normally just rely on `~/.config/gaius/config.toml`.

## Core rule

Gaius writes are local until synced. Other agents and machines may not see a handoff,
task list, note, or decision until `gaius sync` commits and pushes it. Reads may be
stale until sync pulls first.

If using MCP tools, call `sync` before shared reads and immediately after writes. A
successful MCP sync returns `ok: true`; `clean: true` means there are no remaining
uncommitted memory changes. If sync fails, report the error and do not claim other
agents can see the update.

Codex note: some Codex sessions expose MCP tools lazily. If a broad tool search
finds only part of Gaius's expected MCP tool set, run an exact tool search for the
missing names such as `list_projects read_doc task_status gaius` before falling back
to the CLI.

## Recall

```bash
gaius sync                             # pull latest shared memory first
gaius search "<query>"                  # keyword search across the whole store
gaius search "<query>" --project foo    # scope to one project
gaius search "<query>" --full           # print full document bodies, not snippets
gaius show <id-or-path>                 # read one document by id or store-relative path
gaius state <project>                   # read a project's STATE.md + recent decisions
gaius projects                          # list all projects, most recently touched first
```

Search terms are quoted and OR-joined for recall, so punctuation and quotes in the
query are safe — no need to escape anything.

## Save

```bash
gaius add "<durable fact>" --tags a,b --topic <topic>       # global fact
gaius add "<durable fact>" --project <project>               # project-scoped fact
gaius decide <project> "<decision text>"                     # log a decision
gaius handoff <project> --message "<summary and next steps>" # end-of-session handoff
gaius handoff <project> --stdin < notes.md                   # handoff from a longer doc
gaius sync                                                   # publish writes
```

Only save things worth recalling in a future, unrelated session: facts, decisions,
constraints, credentials locations, standing preferences. Don't save ephemeral
in-progress task state — that belongs in the current conversation, not gaius.

## Delegate (escape hatch)

```bash
gaius task <project> "<prompt>"     # background agent task, runs in projects/<project>
gaius task --status <task-id>       # check a running/finished task
gaius task --list                   # list recent tasks
```

Use this for file-heavy work that doesn't fit the narrow memory tools above:
transcription, OCR, bulk re-analysis of artifacts. Output lands in
`projects/<project>/notes/tasks/<task-id>.md` and gets indexed automatically.

## Housekeeping

```bash
gaius sync                # pull -> commit -> push against the configured remote
gaius doctor               # store/git/index/embedder health check
gaius index --rebuild      # rebuild the derived search index from scratch
```

Always use `gaius sync` instead of raw `git` commands against the memory store — it
stops loudly on conflicts instead of leaving the repo mid-merge.
"""


@cli.command()
def doctor() -> None:
    click.echo(doctor_report(load_config()).rstrip())


@cli.command()
@click.argument("project", required=False)
@click.argument("prompt", required=False)
@click.option("--status", "status_id", default=None)
@click.option("--list", "list_requested", is_flag=True)
def task(project: str | None, prompt: str | None, status_id: str | None, list_requested: bool) -> None:
    config = load_config()
    try:
        if list_requested:
            for item in list_tasks(config):
                click.echo(f"{item['task_id']}\t{item['status']}\t{item['project']}\t{item['started']}")
            return
        if status_id:
            status = task_status(config, status_id)
            click.echo(f"Task: {status['task_id']}")
            click.echo(f"Status: {status['status']}")
            if "exit_code" in status:
                click.echo(f"Exit code: {status['exit_code']}")
            click.echo(f"Log: {status['log_path']}")
            if status["log_tail"]:
                click.echo("\n".join(status["log_tail"]))
            return
        if not project or not prompt:
            raise click.UsageError("Provide <project> <prompt>, --status <task_id>, or --list")
        task_id = run_task(config, project, prompt)
        status = task_status(config, task_id)
        click.echo(f"Task: {task_id}")
        click.echo(f"Log: {status['log_path']}")
    except TaskError as exc:
        raise click.ClickException(str(exc)) from exc

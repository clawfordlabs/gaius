from __future__ import annotations

import sys

import click

from .config import load_config
from .doctor import doctor_report
from .indexer import index_store, search
from .store import add_memory, decide as decide_memory, handoff as handoff_memory
from .store import init_store, list_projects, project_state, read_doc
from .sync import SyncError, sync as sync_memory


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
@click.argument("target", required=False, type=click.Choice(["claude", "agents", "generic"]))
def stub(target: str | None) -> None:
    target = target or "generic"
    click.echo(
        f"""## Gaius Memory Instructions for {STUBS[target]}

- Read project state at session start with `gaius state <project>` or by opening `projects/<project>/STATE.md`.
- Use `gaius search "<query>"` before relying on memory or asking the user to repeat durable context.
- Add durable facts with `gaius add "<text>" --tags a,b --topic <topic>` or `--project <project>`.
- Log decisions with `gaius decide <project> "<decision>"`.
- Before ending work, run `gaius handoff <project> --message "<summary and next steps>"`.
- Use `gaius sync` instead of raw git commands for the memory repo.
"""
    )


@cli.command()
def doctor() -> None:
    click.echo(doctor_report(load_config()).rstrip())

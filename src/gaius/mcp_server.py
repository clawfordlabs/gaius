from __future__ import annotations

from argparse import ArgumentParser
from dataclasses import replace
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import load_config
from .indexer import search
from .remote_operations import RemoteOperations
from .remote_validation import (
    RemoteValidationError,
    resolve_store_document,
    store_relative_path,
    validate_document_reference,
    validate_segment,
    validate_text,
)
from . import store as store_ops
from . import sync as sync_ops
from . import tasks as task_ops


READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def build_local_server():
    mcp = FastMCP("gaius")

    @mcp.tool()
    def search_memory(
        query: str,
        project: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        config = load_config()
        return [
            {
                "path": str(result.path),
                "title": result.title,
                "heading": result.heading,
                "snippet": result.snippet,
                "score": result.score,
            }
            for result in search(config.store, config, query, project, limit)
        ]

    @mcp.tool()
    def add_memory(
        text: str,
        tags: list[str] | None = None,
        topic: str | None = None,
        project: str | None = None,
    ) -> str:
        return str(store_ops.add_memory(load_config(), text, tags or [], topic, project))

    @mcp.tool()
    def get_project_state(project: str) -> str:
        return store_ops.project_state(load_config(), project)

    @mcp.tool()
    def handoff(project: str, summary: str) -> str:
        return str(store_ops.handoff(load_config(), project, summary))

    @mcp.tool()
    def log_decision(project: str, text: str) -> str:
        return str(store_ops.decide(load_config(), project, text))

    @mcp.tool()
    def list_projects() -> list[str]:
        return [project for project, _ in store_ops.list_projects(load_config())]

    @mcp.tool()
    def read_doc(path: str) -> str:
        return store_ops.read_doc(load_config(), path)

    @mcp.tool()
    def sync(message: str | None = None) -> dict:
        config = load_config()
        try:
            messages = sync_ops.sync(config.store, message)
        except sync_ops.SyncError as exc:
            return {"ok": False, "messages": [], "error": str(exc)}
        status = sync_ops.git(
            config.store, "status", "--porcelain", check=False
        ).stdout.splitlines()
        return {
            "ok": True,
            "messages": messages,
            "clean": not status,
            "status": status,
        }

    @mcp.tool()
    def run_task(project: str, prompt: str) -> dict:
        config = load_config()
        task_id = task_ops.run_task(config, project, prompt)
        status = task_ops.task_status(config, task_id)
        return {"task_id": task_id, "log_path": status["log_path"]}

    @mcp.tool()
    def task_status(task_id: str) -> dict:
        return task_ops.task_status(load_config(), task_id)

    return mcp


def build_remote_server():
    config = replace(load_config(), externals=(), task_command=())
    operations = RemoteOperations(config.store)
    mcp = FastMCP("gaius")

    def relative_value(path: Path) -> str:
        return path.resolve().relative_to(config.store.resolve()).as_posix()

    @mcp.tool(annotations=READ_ANNOTATIONS)
    def search_memory(
        query: str,
        project: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        validate_text("query", query, 4 * 1024)
        if project is not None:
            validate_segment("project", project)
        if not 1 <= limit <= 50:
            raise RemoteValidationError("limit must be between 1 and 50")

        def run_search():
            output = []
            for result in search(config.store, config, query, project, limit):
                relative = store_relative_path(config.store, result.path)
                if relative is None:
                    continue
                output.append(
                    {
                        "path": relative.as_posix(),
                        "title": result.title,
                        "heading": result.heading,
                        "snippet": result.snippet,
                        "score": result.score,
                    }
                )
            return output

        return operations.read("search_memory", run_search)

    @mcp.tool(annotations=READ_ANNOTATIONS)
    def get_project_state(project: str) -> str:
        validate_segment("project", project)
        return operations.read(
            "get_project_state",
            lambda: store_ops.project_state(config, project, create=False),
        )

    @mcp.tool(annotations=READ_ANNOTATIONS)
    def list_projects() -> list[str]:
        return operations.read(
            "list_projects",
            lambda: [name for name, _ in store_ops.list_projects(config)],
        )

    @mcp.tool(annotations=READ_ANNOTATIONS)
    def read_doc(path: str) -> str:
        validate_document_reference(path)
        return operations.read(
            "read_doc",
            lambda: resolve_store_document(config.store, path).read_text(
                errors="replace"
            ),
        )

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    def add_memory(
        text: str,
        tags: list[str] | None = None,
        topic: str | None = None,
        project: str | None = None,
    ) -> dict:
        """Add a memory; success means its Git commit was pushed upstream."""
        validate_text("memory", text, 64 * 1024)
        clean_tags = tags or []
        if len(clean_tags) > 32:
            raise RemoteValidationError("tags must contain at most 32 values")
        for tag in clean_tags:
            validate_segment("tag", tag)
        if topic is not None:
            validate_segment("topic", topic)
        if project is not None:
            validate_segment("project", project)
        return operations.write(
            "add_memory",
            lambda: relative_value(
                store_ops.add_memory(config, text, clean_tags, topic, project)
            ),
        )

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    def handoff(project: str, summary: str) -> dict:
        """Record a handoff; success means its Git commit was pushed upstream."""
        validate_segment("project", project)
        validate_text("handoff", summary, 64 * 1024)
        return operations.write(
            "handoff",
            lambda: relative_value(store_ops.handoff(config, project, summary)),
        )

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    def log_decision(project: str, text: str) -> dict:
        """Log a decision; success means its Git commit was pushed upstream."""
        validate_segment("project", project)
        validate_text("decision", text, 16 * 1024)
        return operations.write(
            "log_decision",
            lambda: relative_value(store_ops.decide(config, project, text)),
        )

    return mcp


def build_server(profile: str = "local"):
    if profile == "local":
        return build_local_server()
    if profile == "remote":
        return build_remote_server()
    raise ValueError(f"Unknown MCP profile: {profile}")


def main() -> None:
    parser = ArgumentParser(prog="gaius-mcp")
    parser.add_argument(
        "--profile",
        choices=("local", "remote"),
        default="local",
    )
    args = parser.parse_args()
    build_server(args.profile).run()

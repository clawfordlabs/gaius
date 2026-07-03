from __future__ import annotations

from .config import load_config
from .indexer import search
from . import store as store_ops
from mcp.server.fastmcp import FastMCP


def build_server():
    mcp = FastMCP("gaius")

    @mcp.tool()
    def search_memory(query: str, project: str | None = None, limit: int = 10) -> list[dict]:
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
    def add_memory(text: str, tags: list[str] | None = None, topic: str | None = None, project: str | None = None) -> str:
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

    return mcp


def main() -> None:
    build_server().run()

from mcp.types import ToolAnnotations

from gaius.mcp_server import build_server


REMOTE_TOOLS = {
    "search_memory",
    "add_memory",
    "get_project_state",
    "handoff",
    "log_decision",
    "list_projects",
    "read_doc",
}


def tools(server):
    return getattr(getattr(server, "_tool_manager"), "_tools")


def test_remote_profile_exposes_exact_safe_tool_set(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIUS_MEMORY_DIR", str(tmp_path / "memory"))

    remote = tools(build_server("remote"))
    local = tools(build_server("local"))

    assert set(remote) == REMOTE_TOOLS
    assert {"sync", "run_task", "task_status"} <= set(local)


def test_remote_profile_annotations_match_access_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIUS_MEMORY_DIR", str(tmp_path / "memory"))
    remote = tools(build_server("remote"))

    for name in {"search_memory", "get_project_state", "list_projects", "read_doc"}:
        assert remote[name].annotations == ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    for name in {"add_memory", "handoff", "log_decision"}:
        assert remote[name].annotations == ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )

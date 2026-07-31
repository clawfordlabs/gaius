from pathlib import Path
import subprocess

from mcp.types import ToolAnnotations
import pytest

from gaius.indexer import SearchResult
from gaius.mcp_server import build_server
from gaius.remote_validation import RemoteValidationError


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


def run_git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


@pytest.fixture
def remote_mcp(tmp_path, monkeypatch):
    bare_remote = tmp_path / "memory.git"
    store = tmp_path / "memory"
    subprocess.run(
        ["git", "init", "--bare", str(bare_remote)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "clone", str(bare_remote), str(store)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    run_git(store, "config", "user.email", "remote-mcp@example.com")
    run_git(store, "config", "user.name", "Remote MCP")
    (store / ".gitignore").write_text(".gaius/\n")
    (store / "global").mkdir()
    (store / "projects").mkdir()
    run_git(store, "add", "-A")
    run_git(store, "commit", "-m", "initialize")
    run_git(store, "push", "-u", "origin", "HEAD")
    branch = run_git(store, "branch", "--show-current")
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(bare_remote),
            "symbolic-ref",
            "HEAD",
            f"refs/heads/{branch}",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    monkeypatch.setenv("GAIUS_MEMORY_DIR", str(store))
    monkeypatch.setenv("GAIUS_EMBEDDER", "none")
    server_tools = tools(build_server("remote"))
    return {
        "tools": server_tools,
        "store": store,
        "remote": bare_remote,
        "root": tmp_path,
    }


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


def test_remote_write_descriptions_define_published_success(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIUS_MEMORY_DIR", str(tmp_path / "memory"))
    remote = tools(build_server("remote"))

    for name in {"add_memory", "handoff", "log_decision"}:
        description = remote[name].description or ""
        assert "push" in description.lower()


def test_remote_descriptions_distinguish_notes_from_project_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GAIUS_MEMORY_DIR", str(tmp_path / "memory"))
    remote = tools(build_server("remote"))

    assert "does not update project state" in remote["add_memory"].description.lower()
    assert "canonical project state" in remote["handoff"].description.lower()
    assert "does not include project notes" in (
        remote["get_project_state"].description.lower()
    )


def test_remote_add_memory_pushes_before_success(remote_mcp):
    result = remote_mcp["tools"]["add_memory"].fn(
        "ChatGPT remote write",
        ["remote"],
        "tooling",
        None,
    )

    assert result["ok"] is True
    assert result["published"] is True
    assert "ChatGPT remote write" in subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote_mcp["remote"]),
            "grep",
            "ChatGPT remote write",
            "HEAD",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def push_fresh_content(remote_mcp) -> None:
    other = remote_mcp["root"] / "other"
    subprocess.run(
        ["git", "clone", str(remote_mcp["remote"]), str(other)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    run_git(other, "config", "user.email", "other@example.com")
    run_git(other, "config", "user.name", "Other Writer")
    note = other / "global" / "fresh.md"
    note.parent.mkdir()
    note.write_text("# Fresh\n\nfreshneedle from another host\n")
    project = other / "projects" / "synced"
    project.mkdir(parents=True)
    (project / "STATE.md").write_text("# Synced\n\nCurrent remote state\n")
    (project / "DECISIONS.md").write_text("# Synced Decisions\n\n")
    run_git(other, "add", "-A")
    run_git(other, "commit", "-m", "add fresh content")
    run_git(other, "push")


def test_remote_reads_pull_fresh_content(remote_mcp):
    push_fresh_content(remote_mcp)
    remote_tools = remote_mcp["tools"]

    results = remote_tools["search_memory"].fn("freshneedle", None, 10)
    document = remote_tools["read_doc"].fn("global/fresh.md")
    state = remote_tools["get_project_state"].fn("synced")

    assert results[0]["path"] == "global/fresh.md"
    assert "freshneedle from another host" in document
    assert "Current remote state" in state


def test_remote_search_omits_deleted_indexed_memory(remote_mcp):
    remote_tools = remote_mcp["tools"]
    added = remote_tools["add_memory"].fn(
        "memory that will be deleted on another host",
        [],
        "cleanup",
        None,
    )
    assert remote_tools["search_memory"].fn("deleted another host", None, 10)

    other = remote_mcp["root"] / "deleter"
    subprocess.run(
        ["git", "clone", str(remote_mcp["remote"]), str(other)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    run_git(other, "config", "user.email", "deleter@example.com")
    run_git(other, "config", "user.name", "Remote Deleter")
    (other / added["value"]).unlink()
    run_git(other, "add", "-A")
    run_git(other, "commit", "-m", "delete indexed memory")
    run_git(other, "push")

    assert remote_tools["search_memory"].fn("deleted another host", None, 10) == []


def test_remote_project_state_does_not_create_missing_project(remote_mcp):
    store = remote_mcp["store"]

    with pytest.raises(FileNotFoundError):
        remote_mcp["tools"]["get_project_state"].fn("missing")

    assert not (store / "projects" / "missing").exists()


@pytest.mark.parametrize("linked_name", ["STATE.md", "DECISIONS.md"])
def test_remote_project_state_rejects_escaping_file_symlinks(
    remote_mcp, linked_name
):
    store = remote_mcp["store"]
    project = store / "projects" / "linked"
    project.mkdir()
    outside = remote_mcp["root"] / f"outside-{linked_name}"
    outside.write_text("- host secret\n")
    for name in {"STATE.md", "DECISIONS.md"}:
        path = project / name
        if name == linked_name:
            path.symlink_to(outside)
        else:
            path.write_text(f"# Safe {name}\n")
    run_git(store, "add", "-A")
    run_git(store, "commit", "-m", "add linked project file")
    run_git(store, "push")

    with pytest.raises(RemoteValidationError):
        remote_mcp["tools"]["get_project_state"].fn("linked")


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("add_memory", ("outside note", [], None, "linked")),
        ("handoff", ("linked", "outside handoff")),
        ("log_decision", ("linked", "outside decision")),
    ],
)
def test_remote_writes_reject_escaping_project_symlink(
    remote_mcp, tool_name, args
):
    store = remote_mcp["store"]
    outside = remote_mcp["root"] / "outside-project"
    outside.mkdir()
    (outside / "STATE.md").write_text("# Outside\n")
    (outside / "DECISIONS.md").write_text("# Outside Decisions\n")
    (outside / "notes").mkdir()
    (outside / "artifacts").mkdir()
    (store / "projects" / "linked").symlink_to(outside, target_is_directory=True)
    run_git(store, "add", "-A")
    run_git(store, "commit", "-m", "add linked project")
    run_git(store, "push")
    before = {
        path.relative_to(outside): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }

    result = remote_mcp["tools"][tool_name].fn(*args)

    after = {
        path.relative_to(outside): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }
    assert result["ok"] is False
    assert before == after
    assert run_git(store, "status", "--porcelain") == ""


def test_remote_search_filters_external_results(remote_mcp, monkeypatch):
    internal = remote_mcp["store"] / "global" / "internal.md"
    internal.write_text("inside")
    run_git(remote_mcp["store"], "add", "-A")
    run_git(remote_mcp["store"], "commit", "-m", "add internal result")
    run_git(remote_mcp["store"], "push")
    outside = remote_mcp["root"] / "outside.md"
    outside.write_text("outside")
    monkeypatch.setattr(
        "gaius.mcp_server.search",
        lambda *_args, **_kwargs: [
            SearchResult(
                path=Path("global/internal.md"),
                title="Internal",
                heading="",
                snippet="inside",
                score=1.0,
            ),
            SearchResult(
                path=outside.resolve(),
                title="External",
                heading="",
                snippet="outside",
                score=0.5,
            ),
        ],
    )

    results = remote_mcp["tools"]["search_memory"].fn("inside", None, 10)

    assert [item["path"] for item in results] == ["global/internal.md"]


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("read_doc", ("../escape.md",)),
        ("read_doc", ("/etc/passwd",)),
        ("read_doc", ("global/*.md",)),
        ("get_project_state", ("../escape",)),
        ("get_project_state", ("x" * 129,)),
        ("add_memory", ("text", [], None, "../escape")),
        ("add_memory", ("text", [], "../escape", None)),
        ("add_memory", ("text", [], None, ".git")),
        ("add_memory", ("text", [], ".gaius", None)),
        ("add_memory", ("text", [], None, ".GIT")),
        ("add_memory", ("text", [], ".GAIUS", None)),
        ("add_memory", ("text", ["../escape"], None, None)),
        ("handoff", (".gaius", "text")),
        ("log_decision", (".git", "text")),
        ("add_memory", ("text", ["tag"] * 33, None, None)),
        ("add_memory", ("x" * (64 * 1024 + 1), [], None, None)),
        ("handoff", ("project", "x" * (64 * 1024 + 1))),
        ("log_decision", ("project", "x" * (16 * 1024 + 1))),
        ("search_memory", ("x" * (4 * 1024 + 1), None, 10)),
        ("search_memory", ("query", None, 0)),
        ("search_memory", ("query", None, 51)),
    ],
)
def test_remote_tools_reject_unsafe_inputs_before_git_changes(
    remote_mcp, tool_name, args
):
    store = remote_mcp["store"]
    before_head = run_git(store, "rev-parse", "HEAD")

    with pytest.raises(RemoteValidationError):
        remote_mcp["tools"][tool_name].fn(*args)

    assert run_git(store, "rev-parse", "HEAD") == before_head
    assert run_git(store, "status", "--porcelain") == ""

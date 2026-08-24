from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject


@dataclass
class CliResult:
    exit_code: int
    output: str


def run_cli(memory_dir: Path, *args: str):
    env = {**os.environ, "GAIUS_MEMORY_DIR": str(memory_dir), "GAIUS_EMBEDDER": "none"}
    gaius = Path(sys.executable).with_name("gaius")
    result = subprocess.run([str(gaius), *args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    return CliResult(result.returncode, result.stdout)


def test_init_add_search_round_trip_fts_only(tmp_path: Path):
    store = tmp_path / "memory"

    result = run_cli(store, "init")
    assert result.exit_code == 0, result.output
    assert (store / "global").is_dir()
    assert (store / "projects").is_dir()
    assert not (store / "vaults").exists()
    assert (store / ".gaius" / "index.db").exists()

    result = run_cli(
        store,
        "add",
        "Restic to B2 needs B2_ACCOUNT_ID and B2_ACCOUNT_KEY.",
        "--tags",
        "tooling,backup",
        "--topic",
        "tooling",
    )
    assert result.exit_code == 0, result.output
    memory_path = Path(result.output.strip().splitlines()[-1])
    assert memory_path.exists()
    assert "tags: [tooling, backup]" in memory_path.read_text()

    result = run_cli(store, "search", "B2_ACCOUNT_KEY", "--limit", "3")
    assert result.exit_code == 0, result.output
    assert "Restic to B2" in result.output
    assert str(memory_path.relative_to(store)) in result.output

    result = run_cli(store, "show", memory_path.name.removesuffix(".md"))
    assert result.exit_code == 0, result.output
    assert "B2_ACCOUNT_ID" in result.output


def test_project_state_handoff_decide_and_projects(tmp_path: Path):
    store = tmp_path / "memory"
    assert run_cli(store, "init").exit_code == 0

    result = run_cli(store, "handoff", "gaius", "--message", "Implemented index skeleton.")
    assert result.exit_code == 0, result.output
    state_path = store / "projects" / "gaius" / "STATE.md"
    state_text = state_path.read_text()
    assert "# Gaius" in state_text
    assert "Implemented index skeleton." in state_text
    assert "Session Handoff" in state_text

    result = run_cli(store, "decide", "gaius", "Use markdown files as canonical store.")
    assert result.exit_code == 0, result.output
    decisions_text = (store / "projects" / "gaius" / "DECISIONS.md").read_text()
    assert "Use markdown files as canonical store." in decisions_text

    result = run_cli(store, "state", "gaius")
    assert result.exit_code == 0, result.output
    assert "Implemented index skeleton." in result.output
    assert "Use markdown files as canonical store." in result.output

    result = run_cli(store, "projects")
    assert result.exit_code == 0, result.output
    assert "gaius" in result.output


def test_decide_rejects_multiline_text(tmp_path: Path):
    store = tmp_path / "memory"
    assert run_cli(store, "init").exit_code == 0

    result = run_cli(store, "decide", "gaius", "First line.\nSecond line.")

    assert result.exit_code != 0
    assert "Decision text must be a single line." in result.output
    assert "Traceback" not in result.output
    assert not (store / "projects" / "gaius" / "DECISIONS.md").exists()


def test_incremental_reindex_updates_changed_markdown(tmp_path: Path):
    store = tmp_path / "memory"
    assert run_cli(store, "init").exit_code == 0
    doc = store / "global" / "tooling" / "manual-note.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Manual\n\nFirst phrase about alpha.\n")

    result = run_cli(store, "index")
    assert result.exit_code == 0, result.output
    result = run_cli(store, "search", "alpha")
    assert "manual-note.md" in result.output

    doc.write_text("# Manual\n\nSecond phrase about beta.\n")
    result = run_cli(store, "index")
    assert result.exit_code == 0, result.output
    result = run_cli(store, "search", "beta")
    assert result.exit_code == 0, result.output
    assert "Second phrase" in result.output


def test_chunk_markdown_by_heading_with_context_prefix():
    from gaius.indexer import chunk_markdown

    text = "# Root\n\nIntro text.\n\n## Details\n\n" + "word " * 950
    chunks = chunk_markdown("global/topic/doc.md", text, max_tokens=400)

    assert len(chunks) >= 3
    assert chunks[0].heading == "Root"
    assert chunks[0].text.startswith("Path: global/topic/doc.md\nHeading: Root\n\n")
    assert all(len(chunk.text.split()) <= 430 for chunk in chunks)
    assert any(chunk.heading == "Root > Details" for chunk in chunks)


def test_sync_happy_path_in_tmp_git_repo(tmp_path: Path):
    store = tmp_path / "memory"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)

    assert run_cli(store, "init").exit_code == 0
    subprocess.run(["git", "-C", str(store), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(store), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(store), "remote", "add", "origin", str(remote)], check=True)

    result = run_cli(store, "add", "Sync test memory", "--topic", "tooling")
    assert result.exit_code == 0, result.output

    result = run_cli(store, "sync", "--message", "test sync")
    assert result.exit_code == 0, result.output
    assert "Committed" in result.output
    assert "Pushed" in result.output

    pushed = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "--oneline"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert "test sync" in pushed


def init_synced_store(store: Path, remote: Path) -> str:
    assert run_cli(store, "init").exit_code == 0
    subprocess.run(["git", "-C", str(store), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(store), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(store), "remote", "add", "origin", str(remote)], check=True)
    result = run_cli(store, "handoff", "demo", "--message", "Initial handoff.")
    assert result.exit_code == 0, result.output
    result = run_cli(store, "decide", "demo", "Initial decision.")
    assert result.exit_code == 0, result.output
    result = run_cli(store, "sync")
    assert result.exit_code == 0, result.output
    return subprocess.run(
        ["git", "-C", str(store), "branch", "--show-current"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def clone_store(remote: Path, branch: str, destination: Path) -> None:
    subprocess.run(["git", "clone", "--branch", branch, str(remote), str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(destination), "config", "user.name", "Test User"], check=True)


def prepend_handoff(store: Path, timestamp: str, message: str) -> None:
    state = store / "projects" / "demo" / "STATE.md"
    existing = state.read_text()
    first_line, rest = existing.split("\n", 1)
    section = f"## Session Handoff - {timestamp}\n\n{message}\n\n"
    state.write_text(f"{first_line}\n\n{section}{rest.lstrip()}")


def append_decision(store: Path, timestamp: str, message: str) -> None:
    decisions = store / "projects" / "demo" / "DECISIONS.md"
    with decisions.open("a") as handle:
        handle.write(f"- {timestamp} - {message}\n")


def test_sync_merges_concurrent_timestamped_handoffs(tmp_path: Path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    branch = init_synced_store(primary, remote)
    clone_store(remote, branch, secondary)

    prepend_handoff(primary, "2026-08-24T01:00:00+00:00", "Older local handoff.")
    prepend_handoff(secondary, "2026-08-24T02:00:00+00:00", "Newer remote handoff.")

    result = run_cli(secondary, "sync")
    assert result.exit_code == 0, result.output
    result = run_cli(primary, "sync")
    assert result.exit_code == 0, result.output
    assert "Merged concurrent handoffs" in result.output

    state = (primary / "projects" / "demo" / "STATE.md").read_text()
    assert state.index("Newer remote handoff.") < state.index("Older local handoff.")


def test_sync_merges_handoffs_with_level_two_headings(tmp_path: Path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    branch = init_synced_store(primary, remote)
    clone_store(remote, branch, secondary)

    prepend_handoff(
        primary, "2026-08-24T01:00:00+00:00", "Local handoff.\n\n## Next Steps\n\n- Complete local work."
    )
    prepend_handoff(
        secondary, "2026-08-24T02:00:00+00:00", "Remote handoff.\n\n## Findings\n\n- Complete remote work."
    )

    assert run_cli(secondary, "sync").exit_code == 0
    result = run_cli(primary, "sync")
    assert result.exit_code == 0, result.output
    state = (primary / "projects" / "demo" / "STATE.md").read_text()
    assert "Remote handoff.\n\n## Findings\n\n- Complete remote work." in state
    assert "Local handoff.\n\n## Next Steps\n\n- Complete local work." in state


def test_sync_merges_concurrent_timestamped_decisions(tmp_path: Path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    branch = init_synced_store(primary, remote)
    clone_store(remote, branch, secondary)

    append_decision(primary, "2026-08-24T01:00:00+00:00", "Local decision.")
    append_decision(secondary, "2026-08-24T02:00:00+00:00", "Remote decision.")

    result = run_cli(secondary, "sync")
    assert result.exit_code == 0, result.output
    result = run_cli(primary, "sync")
    assert result.exit_code == 0, result.output
    assert "Merged concurrent decisions" in result.output

    decisions = (primary / "projects" / "demo" / "DECISIONS.md").read_text()
    assert decisions.index("Local decision.") < decisions.index("Remote decision.")


def test_sync_rejects_legacy_multiline_decision_during_concurrent_merge(tmp_path: Path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    branch = init_synced_store(primary, remote)
    append_decision(primary, "2026-08-24T00:00:00+00:00", "Base decision.\n\n- Supporting rationale.")
    assert run_cli(primary, "sync").exit_code == 0
    clone_store(remote, branch, secondary)

    append_decision(primary, "2026-08-24T01:00:00+00:00", "Local decision.")
    append_decision(secondary, "2026-08-24T02:00:00+00:00", "Remote decision.")

    assert run_cli(secondary, "sync").exit_code == 0
    result = run_cli(primary, "sync")
    assert result.exit_code != 0
    assert "Git merge conflict during pull" in result.output




def test_timestamped_entry_merges_reject_duplicate_timestamps():
    from gaius.sync import merge_decisions, merge_handoffs

    handoff = "## Session Handoff - 2026-08-24T01:00:00+00:00\n\nDuplicate handoff.\n\n"
    decision = "- 2026-08-24T01:00:00+00:00 - Duplicate decision.\n"

    assert merge_handoffs("# Demo\n\n", f"# Demo\n\n{handoff}{handoff}", "# Demo\n\n") is None
    assert merge_decisions("# Demo Decisions\n\n", f"# Demo Decisions\n\n{decision}{decision}", "# Demo Decisions\n\n") is None

def test_sync_rejects_handoff_deletion_during_concurrent_merge(tmp_path: Path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    branch = init_synced_store(primary, remote)
    clone_store(remote, branch, secondary)

    state = primary / "projects" / "demo" / "STATE.md"
    before, after = state.read_text().split("## Current Status", 1)
    state.write_text(before.split("## Session Handoff", 1)[0] + "## Current Status" + after)
    prepend_handoff(primary, "2026-08-24T01:00:00+00:00", "Local replacement handoff.")
    prepend_handoff(secondary, "2026-08-24T02:00:00+00:00", "Remote additive handoff.")

    assert run_cli(secondary, "sync").exit_code == 0
    result = run_cli(primary, "sync")
    assert result.exit_code != 0
    assert "Git merge conflict during pull" in result.output


def test_sync_rejects_decision_deletion_during_concurrent_merge(tmp_path: Path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    branch = init_synced_store(primary, remote)
    clone_store(remote, branch, secondary)

    decisions = primary / "projects" / "demo" / "DECISIONS.md"
    decisions.write_text("\n".join(line for line in decisions.read_text().splitlines() if "Initial decision." not in line) + "\n")
    append_decision(primary, "2026-08-24T01:00:00+00:00", "Local replacement decision.")
    append_decision(secondary, "2026-08-24T02:00:00+00:00", "Remote additive decision.")

    assert run_cli(secondary, "sync").exit_code == 0
    result = run_cli(primary, "sync")
    assert result.exit_code != 0
    assert "Git merge conflict during pull" in result.output


def test_sync_leaves_non_handoff_state_edits_for_manual_resolution(tmp_path: Path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    branch = init_synced_store(primary, remote)
    clone_store(remote, branch, secondary)

    for store, status in ((primary, "Local status."), (secondary, "Remote status.")):
        state = store / "projects" / "demo" / "STATE.md"
        state.write_text(state.read_text().replace("Not yet recorded.", status))

    assert run_cli(secondary, "sync").exit_code == 0
    result = run_cli(primary, "sync")
    assert result.exit_code != 0
    assert "Git merge conflict during pull" in result.output


def test_sync_refuses_retry_during_unresolved_merge(tmp_path: Path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    branch = init_synced_store(primary, remote)
    clone_store(remote, branch, secondary)

    for store, status in ((primary, "Local status."), (secondary, "Remote status.")):
        state = store / "projects" / "demo" / "STATE.md"
        state.write_text(state.read_text().replace("Not yet recorded.", status))

    assert run_cli(secondary, "sync").exit_code == 0
    assert run_cli(primary, "sync").exit_code != 0
    result = run_cli(primary, "sync")
    assert result.exit_code != 0
    assert "unresolved Git operation" in result.output

    conflicts = subprocess.run(
        ["git", "-C", str(primary), "diff", "--name-only", "--diff-filter=U"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()
    assert conflicts == ["projects/demo/STATE.md"]


def run_setup(tmp_path: Path, home: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "HOME": str(home), "PYTHON": sys.executable, "XDG_CONFIG_HOME": str(home / ".config")}
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(repo / "setup")],
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )


def test_setup_preserves_configured_store_path(tmp_path: Path):
    home = tmp_path / "home"
    configured_store = tmp_path / "configured-memory"
    config = home / ".config" / "gaius" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'store_path = "{configured_store}"\n')

    result = run_setup(tmp_path, home)

    assert result.returncode == 0, result.stdout
    assert (configured_store / ".git").exists()
    assert f"Memory store: {configured_store}" in result.stdout
    assert not (home / "memory").exists()
    assert f'store_path = "{configured_store.resolve()}"' in config.read_text()


def test_setup_configured_store_ignores_memory_dir_override(tmp_path: Path):
    home = tmp_path / "home"
    configured_store = tmp_path / "configured-memory"
    override_store = tmp_path / "override-memory"
    config = home / ".config" / "gaius" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'store_path = "{configured_store}"\n')

    result = run_setup(tmp_path, home, {"GAIUS_MEMORY_DIR": str(override_store)})

    assert result.returncode == 0, result.stdout
    assert (configured_store / ".git").exists()
    assert not override_store.exists()
    assert f"Memory store: {configured_store}" in result.stdout


def test_setup_initializes_existing_default_memory_dir(tmp_path: Path):
    home = tmp_path / "home"
    (home / "memory").mkdir(parents=True)

    result = run_setup(tmp_path, home)

    assert result.returncode == 0, result.stdout
    assert (home / "memory" / ".git").exists()
    assert (home / "memory" / "global").is_dir()


def test_setup_path_check_does_not_match_usr_local_bin(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    profile = home / ".profile"
    profile.write_text('export PATH="/usr/local/bin:$PATH"\n')

    result = run_setup(tmp_path, home)

    assert result.returncode == 0, result.stdout
    assert 'export PATH="$HOME/.local/bin:$PATH"' in profile.read_text()


def test_mcp_sync_commits_and_pushes_tmp_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.mcp_server import build_server

    store = tmp_path / "memory"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    monkeypatch.setenv("GAIUS_MEMORY_DIR", str(store))
    monkeypatch.setenv("GAIUS_EMBEDDER", "none")

    assert run_cli(store, "init").exit_code == 0
    subprocess.run(["git", "-C", str(store), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(store), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(store), "remote", "add", "origin", str(remote)], check=True)

    result = run_cli(store, "handoff", "gaius", "--message", "MCP sync handoff")
    assert result.exit_code == 0, result.output

    server = build_server()
    tool = getattr(getattr(server, "_tool_manager"), "_tools")["sync"]
    sync_result = tool.fn("mcp sync test")
    assert sync_result["ok"] is True
    assert sync_result["clean"] is True
    assert any("Committed" in message for message in sync_result["messages"])
    assert any("Pushed" in message for message in sync_result["messages"])

    pushed = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "--oneline"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert "mcp sync test" in pushed


def test_stub_and_doctor_report_required_information(tmp_path: Path):
    store = tmp_path / "memory"
    assert run_cli(store, "init").exit_code == 0

    result = run_cli(store, "stub", "agents")
    assert result.exit_code == 0, result.output
    assert "gaius sync` before reading shared state" in result.output
    assert "Read project state at session start" in result.output
    assert "gaius handoff" in result.output

    result = run_cli(store, "stub", "skill")
    assert result.exit_code == 0, result.output
    assert "name: gaius" in result.output
    assert "Gaius writes are local until synced" in result.output
    assert "Every session MUST sync before its first shared read and immediately after every Gaius write" in result.output.replace("\n", " ")
    assert "Codex note: some Codex sessions expose MCP tools lazily" in result.output
    assert "list_projects read_doc task_status gaius" in result.output

    result = run_cli(store, "doctor")
    assert result.exit_code == 0, result.output
    assert f"Store: {store}" in result.output
    assert "Git:" in result.output
    assert "Index:" in result.output
    assert "Embedder:" in result.output
    assert "Vector index:" in result.output


def test_search_sanitizes_special_fts5_query_syntax(tmp_path: Path):
    store = tmp_path / "memory"
    assert run_cli(store, "init").exit_code == 0
    result = run_cli(store, "add", "What this and that syntax should find safely.", "--topic", "tooling")
    assert result.exit_code == 0, result.output

    result = run_cli(store, "search", 'what\'s "this" AND (that)*', "--limit", "5")
    assert result.exit_code == 0, result.output
    assert "syntax should find safely" in result.output


def test_openai_embedder_selection_and_request(monkeypatch: pytest.MonkeyPatch):
    from gaius.config import Config
    from gaius.embeddings import BaseEmbedder, OpenAIEmbedder, get_embedder

    monkeypatch.setenv("GAIUS_OPENAI_KEY", "secret-token")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    config = Config(
        store=Path("/tmp/memory"),
        embedder="openai",
        openai_base_url="https://api.example.test/v1",
        openai_key_env="GAIUS_OPENAI_KEY",
        openai_model="text-embedding-test",
    )
    embedder = get_embedder(config)
    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder.embed(["one", "two"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["url"] == "https://api.example.test/v1/embeddings"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["body"] == {"model": "text-embedding-test", "input": ["one", "two"]}

    auto_config = Config(
        store=Path("/tmp/memory"),
        embedder="auto",
        openai_base_url="https://api.example.test/v1",
        openai_key_env="GAIUS_OPENAI_KEY",
        openai_model="text-embedding-test",
    )
    monkeypatch.setattr("gaius.embeddings.FastEmbedEmbedder", lambda: (_ for _ in ()).throw(ImportError("missing")))
    assert isinstance(get_embedder(auto_config), BaseEmbedder)

    with pytest.raises(ValueError, match="openai embedder requires"):
        get_embedder(Config(store=Path("/tmp/memory"), embedder="openai"))


def test_sqlite_vec_vector_search_path_with_fake_embedder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.config import Config
    from gaius.indexer import index_files, search, vector_storage_status

    class FakeEmbedder:
        name = "fake"

        def embed(self, texts: list[str]) -> list[list[float]]:
            vectors = []
            for text in texts:
                if "apple" in text.lower():
                    vectors.append([1.0, 0.0, 0.0])
                elif "banana" in text.lower():
                    vectors.append([0.0, 1.0, 0.0])
                else:
                    vectors.append([0.0, 0.0, 1.0])
            return vectors

    store = tmp_path / "memory"
    assert run_cli(store, "init").exit_code == 0
    apple = store / "global" / "fruit" / "apple.md"
    banana = store / "global" / "fruit" / "banana.md"
    apple.parent.mkdir(parents=True, exist_ok=True)
    apple.write_text("# Apple\n\ncrisp red apple\n")
    banana.write_text("# Banana\n\nsoft yellow banana\n")
    config = Config(store=store, embedder="fake")
    monkeypatch.setattr("gaius.indexer.get_embedder", lambda _config: FakeEmbedder())

    indexed = index_files(store, config, [apple, banana], rebuild=True)
    assert indexed == 2
    assert vector_storage_status(store).startswith("sqlite-vec")

    results = search(store, config, "apple", limit=1)
    assert results
    assert results[0].path.name == "apple.md"


def test_write_config_preserves_existing_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.config import config_file, write_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = config_file()
    path.parent.mkdir(parents=True)
    path.write_text(
        'store_path = "/old/store"\n'
        'embedder = "openai"\n'
        'vault_paths = ["/vault/one"]\n'
        '[openai]\n'
        'base_url = "https://api.example.test/v1"\n'
        'key_env = "GAIUS_OPENAI_KEY"\n'
        'model = "text-embedding-test"\n'
    )

    write_config(tmp_path / "memory")
    text = path.read_text()
    assert f'store_path = "{(tmp_path / "memory").resolve()}"' in text
    assert 'embedder = "openai"' in text
    assert 'vault_paths = ["/vault/one"]' in text
    assert '[openai]' in text
    assert 'model = "text-embedding-test"' in text


def test_pdf_text_is_indexed_and_searchable(tmp_path: Path):
    store = tmp_path / "memory"
    assert run_cli(store, "init").exit_code == 0
    pdf = store / "projects" / "gaius" / "artifacts" / "sample.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = StreamObject()
    stream._data = b"BT /F1 12 Tf 50 250 Td (Gaius PDF searchable orchid text) Tj ET"
    page[NameObject("/Contents")] = writer._add_object(stream)
    with pdf.open("wb") as f:
        writer.write(f)

    result = run_cli(store, "index")
    assert result.exit_code == 0, result.output
    result = run_cli(store, "search", "orchid", "--limit", "3")
    assert result.exit_code == 0, result.output
    assert "sample.pdf" in result.output
    assert "orchid text" in result.output


def test_config_parses_external_roots_and_legacy_vault_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.config import load_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("GAIUS_MEMORY_DIR", raising=False)
    path = tmp_path / "config" / "gaius" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f'store_path = "{tmp_path / "memory"}"\n'
        'index_max_file_mb = 7\n'
        'vault_paths = ["~/legacy-vault"]\n'
        '[[external]]\n'
        'path = "~/Documents/Obsidian Vault"\n'
        'include = ["notes/**"]\n'
        'exclude = ["notes/private/**"]\n'
        '[[external]]\n'
        f'path = "{tmp_path / "client-portal"}"\n'
        'project = "client-portal"\n'
        'exclude = ["node_modules/**"]\n'
        '[fastembed]\n'
        'model = "BAAI/bge-small-en-v1.5"\n'
    )

    config = load_config()

    assert config.store == (tmp_path / "memory").resolve()
    assert config.index_max_file_mb == 7
    assert config.fastembed_model == "BAAI/bge-small-en-v1.5"
    assert len(config.externals) == 3
    assert config.externals[0].path == (Path.home() / "Documents" / "Obsidian Vault").resolve()
    assert config.externals[0].project is None
    assert config.externals[0].include == ("notes/**",)
    assert config.externals[0].exclude == ("notes/private/**",)
    assert config.externals[1].project == "client-portal"
    assert config.externals[1].exclude == ("node_modules/**",)
    assert config.externals[2].path == (Path.home() / "legacy-vault").resolve()
    assert config.externals[2].project is None


def test_external_indexing_paths_project_allowlist_and_size_cap(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.doctor import doctor_report
    from gaius.indexer import index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "external"
    external.mkdir()
    (external / "external.md").write_text("# External\n\nexternalalpha project doc\n")
    (external / "plain.txt").write_text("externaltxt plain text doc\n")
    (external / "script.py").write_text("externalpython should not index\n")
    (external / "huge.md").write_text("hugeexternal " * 500)
    internal = store / "global" / "notes" / "internal.txt"
    config = Config(
        store=store,
        embedder="none",
        externals=(ExternalRoot(path=external.resolve(), project="example-project"),),
        index_max_file_mb=0.001,
    )
    init_store(config, write_user_config=False)
    internal.parent.mkdir(parents=True)
    internal.write_text("internaltext store doc\n")

    assert index_store(store, config, rebuild=True) == 3

    external_results = search(store, config, "externalalpha", project="example-project")
    assert external_results
    assert external_results[0].path == (external / "external.md").resolve()
    assert external_results[0].path.is_absolute()

    text_results = search(store, config, "externaltxt", project="example-project")
    assert text_results
    assert text_results[0].path == (external / "plain.txt").resolve()
    assert search(store, config, "externalpython", project="example-project") == []
    assert search(store, config, "hugeexternal", project="example-project") == []

    internal_results = search(store, config, "internaltext")
    assert internal_results
    assert internal_results[0].path == Path("global/notes/internal.txt")
    assert not internal_results[0].path.is_absolute()

    report = doctor_report(config)
    assert "External roots:" in report
    assert f"{external.resolve()} (exists: yes, project: example-project, indexable: 2)" in report


def test_external_gitignore_is_respected(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")

    from gaius.config import Config, ExternalRoot
    from gaius.indexer import index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "repo"
    external.mkdir()
    subprocess.run(["git", "init"], cwd=external, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (external / ".gitignore").write_text("ignored.md\n")
    (external / "visible.md").write_text("visiblegitignore searchable\n")
    (external / "ignored.md").write_text("ignoredgitignore searchable\n")
    config = Config(store=store, embedder="none", externals=(ExternalRoot(path=external.resolve()),))
    init_store(config, write_user_config=False)

    index_store(store, config, rebuild=True)

    assert search(store, config, "visiblegitignore")
    assert search(store, config, "ignoredgitignore") == []


def test_external_same_named_files_do_not_collide(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import connect, ensure_schema, index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "same.md").write_text("samecollision first root\n")
    (two / "same.md").write_text("samecollision second root\n")
    config = Config(
        store=store,
        embedder="none",
        externals=(ExternalRoot(path=one.resolve()), ExternalRoot(path=two.resolve())),
    )
    init_store(config, write_user_config=False)

    index_store(store, config, rebuild=True)

    results = search(store, config, "samecollision", limit=10)
    paths = {result.path for result in results}
    assert (one / "same.md").resolve() in paths
    assert (two / "same.md").resolve() in paths

    conn = connect(store)
    ensure_schema(conn)
    rows = conn.execute("SELECT path FROM docs WHERE path LIKE ?", (f"{tmp_path}%",)).fetchall()
    conn.close()
    assert {row["path"] for row in rows} == {str((one / "same.md").resolve()), str((two / "same.md").resolve())}


def test_external_include_and_exclude_globs(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "external"
    (external / "keep").mkdir(parents=True)
    (external / "other").mkdir()
    (external / "keep" / "included.md").write_text("includedglob searchable\n")
    (external / "keep" / "drop.md").write_text("excludedglob searchable\n")
    (external / "other" / "outside.md").write_text("outsideglob searchable\n")
    config = Config(
        store=store,
        embedder="none",
        externals=(ExternalRoot(path=external.resolve(), include=("keep/**",), exclude=("keep/drop.md",)),),
    )
    init_store(config, write_user_config=False)

    index_store(store, config, rebuild=True)

    assert search(store, config, "includedglob")
    assert search(store, config, "excludedglob") == []
    assert search(store, config, "outsideglob") == []


def test_external_globs_match_nested_paths(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "external"
    (external / "keep" / "deep").mkdir(parents=True)
    (external / "keep" / "deep" / "included.md").write_text("nestedincluded searchable\n")
    (external / "keep" / "deep" / "secret.md").write_text("nestedsecret searchable\n")
    config = Config(
        store=store,
        embedder="none",
        externals=(ExternalRoot(path=external.resolve(), include=("keep/**",), exclude=("keep/**/secret.md",)),),
    )
    init_store(config, write_user_config=False)

    index_store(store, config, rebuild=True)

    assert search(store, config, "nestedincluded")
    assert search(store, config, "nestedsecret") == []


def test_external_root_inside_store_is_not_double_indexed(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import connect, ensure_schema, index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    init_store(Config(store=store, embedder="none"), write_user_config=False)
    doc = store / "global" / "notes" / "overlap.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("overlapinternal searchable\n")
    config = Config(store=store, embedder="none", externals=(ExternalRoot(path=(store / "global").resolve(), project="wrong"),))

    index_store(store, config, rebuild=True)

    results = search(store, config, "overlapinternal", limit=10)
    assert [result.path for result in results] == [Path("global/notes/overlap.md")]
    conn = connect(store)
    ensure_schema(conn)
    rows = conn.execute("SELECT path, project FROM docs WHERE path LIKE '%overlap.md'").fetchall()
    conn.close()
    assert [(row["path"], row["project"]) for row in rows] == [("global/notes/overlap.md", None)]


def test_doctor_external_count_excludes_store_internal_files(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.doctor import doctor_report
    from gaius.store import init_store

    store = tmp_path / "memory"
    init_store(Config(store=store, embedder="none"), write_user_config=False)
    internal = store / "global" / "notes" / "internal.md"
    internal.parent.mkdir(parents=True, exist_ok=True)
    internal.write_text("internal external overlap\n")
    config = Config(store=store, embedder="none", externals=(ExternalRoot(path=(store / "global").resolve()),))

    report = doctor_report(config)

    assert f"{(store / 'global').resolve()} (exists: yes, project: none, indexable: 0)" in report


def test_nested_external_roots_use_deepest_project(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "doc.md").write_text("nestedproject searchable\n")
    config = Config(
        store=store,
        embedder="none",
        externals=(
            ExternalRoot(path=root.resolve()),
            ExternalRoot(path=nested.resolve(), project="deep"),
        ),
    )
    init_store(config, write_user_config=False)

    index_store(store, config, rebuild=True)

    assert search(store, config, "nestedproject", project="deep")
    assert search(store, config, "nestedproject", project="wrong") == []


def test_external_git_failure_does_not_fallback_to_ignored_rglob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "repo"
    (external / ".git").mkdir(parents=True)
    (external / ".gitignore").write_text("ignored.md\n")
    (external / "ignored.md").write_text("ignoredaftergitfailure searchable\n")

    def failed_git(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"fatal")

    monkeypatch.setattr("gaius.indexer.subprocess.run", failed_git)
    config = Config(store=store, embedder="none", externals=(ExternalRoot(path=external.resolve()),))
    init_store(config, write_user_config=False)

    index_store(store, config, rebuild=True)

    assert search(store, config, "ignoredaftergitfailure") == []


def test_external_git_failure_does_not_purge_existing_indexed_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "repo"
    external.mkdir()
    (external / ".git").mkdir()
    doc = external / "visible.md"
    doc.write_text("gitfailurekeep searchable\n")
    config = Config(store=store, embedder="none", externals=(ExternalRoot(path=external.resolve()),))
    init_store(config, write_user_config=False)

    calls = 0

    def flaky_git(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= 2:
            return subprocess.CompletedProcess([], 0, stdout=b"visible.md\0", stderr=b"")
        return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"fatal")

    monkeypatch.setattr("gaius.indexer.subprocess.run", flaky_git)

    index_store(store, config, rebuild=True)
    assert search(store, config, "gitfailurekeep")

    index_store(store, config, rebuild=False)

    assert search(store, config, "gitfailurekeep")


def test_unavailable_external_root_does_not_purge_existing_indexed_docs(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "external"
    external.mkdir()
    doc = external / "visible.md"
    doc.write_text("missingrootkeep searchable\n")
    config = Config(store=store, embedder="none", externals=(ExternalRoot(path=external.resolve()),))
    init_store(config, write_user_config=False)

    index_store(store, config, rebuild=True)
    assert search(store, config, "missingrootkeep")

    shutil.rmtree(external)
    index_store(store, config, rebuild=False)

    assert search(store, config, "missingrootkeep")


def test_incremental_index_removes_legacy_vault_rows(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import connect, ensure_schema, index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "vault"
    external.mkdir()
    doc = external / "note.md"
    doc.write_text("legacyvault searchable\n")
    config = Config(store=store, embedder="none", externals=(ExternalRoot(path=external.resolve()),))
    init_store(config, write_user_config=False)
    conn = connect(store)
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO docs(path, title, mtime, hash, project, tags, kind, cached_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("vaults/note.md", "note", 0, "old", None, "[]", "markdown", None),
    )
    conn.commit()
    conn.close()

    index_store(store, config, rebuild=False)

    results = search(store, config, "legacyvault", limit=10)
    assert {result.path for result in results} == {doc.resolve()}
    conn = connect(store)
    ensure_schema(conn)
    paths = {row["path"] for row in conn.execute("SELECT path FROM docs").fetchall()}
    conn.close()
    assert "vaults/note.md" not in paths
    assert str(doc.resolve()) in paths


def test_incremental_index_removes_legacy_vault_rows_when_scan_empty(tmp_path: Path):
    from gaius.config import Config
    from gaius.indexer import connect, ensure_schema, index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    config = Config(store=store, embedder="none")
    init_store(config, write_user_config=False)
    conn = connect(store)
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO docs(path, title, mtime, hash, project, tags, kind, cached_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("vaults/old-note.md", "old-note", 0, "old", None, "[]", "markdown", None),
    )
    doc_id = conn.execute("SELECT id FROM docs WHERE path = ?", ("vaults/old-note.md",)).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO chunks(doc_id, chunk_index, heading, text, vector) VALUES (?, ?, ?, ?, ?)",
        (doc_id, 0, "old-note", "legacyempty searchable", None),
    )
    conn.execute(
        "INSERT INTO chunks_fts(rowid, text, path, heading, chunk_id) VALUES (?, ?, ?, ?, ?)",
        (cur.lastrowid, "legacyempty searchable", "vaults/old-note.md", "old-note", cur.lastrowid),
    )
    assert conn.execute("SELECT COUNT(*) AS count FROM docs").fetchone()["count"] == 1
    conn.commit()
    conn.close()

    index_store(store, config, rebuild=False)

    assert search(store, config, "legacyempty") == []
    conn = connect(store)
    ensure_schema(conn)
    rows = conn.execute("SELECT path FROM docs").fetchall()
    conn.close()
    assert [row["path"] for row in rows] == []


def test_vector_config_change_reembeds_unchanged_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.config import Config
    from gaius.indexer import connect, ensure_schema, index_store, search
    from gaius.store import init_store

    class FakeEmbedder:
        name = "fake"

        def __init__(self, vectors: list[list[float]]):
            self.vectors = vectors

        def embed(self, texts: list[str]) -> list[list[float]]:
            return self.vectors[: len(texts)]

    store = tmp_path / "memory"
    init_store(Config(store=store, embedder="none"), write_user_config=False)
    doc = store / "global" / "vectors.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("vectordoc searchable\n")
    config_one = Config(store=store, embedder="fake-one")
    config_two = Config(store=store, embedder="fake-two")
    monkeypatch.setattr("gaius.indexer.get_embedder", lambda _config: FakeEmbedder([[1.0, 0.0]]))

    index_store(store, config_one, rebuild=True)
    conn = connect(store)
    ensure_schema(conn)
    first_vector = conn.execute("SELECT vector FROM chunks WHERE vector IS NOT NULL").fetchone()["vector"]
    conn.close()

    monkeypatch.setattr("gaius.indexer.get_embedder", lambda _config: FakeEmbedder([[0.0, 1.0]]))
    assert search(store, config_two, "vectordoc")

    conn = connect(store)
    ensure_schema(conn)
    second_vector = conn.execute("SELECT vector FROM chunks WHERE vector IS NOT NULL").fetchone()["vector"]
    conn.close()
    assert first_vector != second_vector
    assert json.loads(second_vector) == [0.0, 1.0]


def test_index_store_reembeds_docs_left_vectorless_by_incremental_config_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.config import Config
    from gaius.indexer import connect, ensure_schema, index_files, index_store
    from gaius.store import init_store

    class FakeEmbedder:
        name = "fake"

        def __init__(self, vector: list[float]):
            self.vector = vector

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [self.vector for _ in texts]

    store = tmp_path / "memory"
    init_store(Config(store=store, embedder="none"), write_user_config=False)
    old_doc = store / "global" / "old.md"
    new_doc = store / "global" / "new.md"
    old_doc.write_text("oldvector searchable\n")
    new_doc.write_text("newvector searchable\n")
    config_one = Config(store=store, embedder="fake-one")
    config_two = Config(store=store, embedder="fake-two")
    monkeypatch.setattr("gaius.indexer.get_embedder", lambda _config: FakeEmbedder([1.0, 0.0]))
    index_files(store, config_one, [old_doc], rebuild=True)

    monkeypatch.setattr("gaius.indexer.get_embedder", lambda _config: FakeEmbedder([0.0, 1.0]))
    index_files(store, config_two, [new_doc])
    conn = connect(store)
    ensure_schema(conn)
    null_before = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM chunks
        JOIN docs ON docs.id = chunks.doc_id
        WHERE docs.path = ? AND chunks.vector IS NULL
        """,
        ("global/old.md",),
    ).fetchone()["count"]
    conn.close()
    assert null_before == 1

    index_store(store, config_two, rebuild=False)

    conn = connect(store)
    ensure_schema(conn)
    vector = conn.execute(
        """
        SELECT chunks.vector
        FROM chunks
        JOIN docs ON docs.id = chunks.doc_id
        WHERE docs.path = ?
        """,
        ("global/old.md",),
    ).fetchone()["vector"]
    conn.close()
    assert json.loads(vector) == [0.0, 1.0]


def test_index_freshness_marks_embedder_change_and_missing_vectors_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.config import Config
    from gaius.indexer import connect, ensure_schema, index_files, index_freshness, index_store
    from gaius.store import init_store

    class FakeEmbedder:
        name = "fake"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    store = tmp_path / "memory"
    init_store(Config(store=store, embedder="none"), write_user_config=False)
    doc = store / "global" / "freshness.md"
    doc.write_text("freshvector searchable\n")
    config_one = Config(store=store, embedder="fake-one")
    config_two = Config(store=store, embedder="fake-two")
    monkeypatch.setattr("gaius.indexer.get_embedder", lambda _config: FakeEmbedder())

    index_files(store, config_one, [doc], rebuild=True)
    assert index_freshness(store, config_one) == (1, 1)
    assert index_freshness(store, config_two) == (1, 0)

    index_store(store, config_two, rebuild=False)
    conn = connect(store)
    ensure_schema(conn)
    conn.execute("UPDATE chunks SET vector = NULL")
    conn.commit()
    conn.close()

    assert index_freshness(store, config_two) == (1, 0)


def test_index_freshness_ignores_vectors_when_runtime_embedder_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from gaius.config import Config
    from gaius.indexer import connect, ensure_schema, index_files, index_freshness
    from gaius.store import init_store

    class FakeEmbedder:
        name = "fake"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    class NoEmbedder:
        name = "none"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return []

    store = tmp_path / "memory"
    init_store(Config(store=store, embedder="none"), write_user_config=False)
    doc = store / "global" / "autofallback.md"
    doc.write_text("autofallback searchable\n")
    vector_config = Config(store=store, embedder="fake-one")
    auto_config = Config(store=store, embedder="auto")

    monkeypatch.setattr("gaius.indexer.get_embedder", lambda _config: FakeEmbedder())
    index_files(store, vector_config, [doc], rebuild=True)

    conn = connect(store)
    ensure_schema(conn)
    conn.execute("UPDATE chunks SET vector = NULL")
    conn.commit()
    conn.close()

    monkeypatch.setattr("gaius.indexer.get_embedder", lambda _config: NoEmbedder())

    assert index_freshness(store, auto_config) == (1, 1)


def test_external_project_change_updates_unchanged_docs(tmp_path: Path):
    from gaius.config import Config, ExternalRoot
    from gaius.indexer import index_freshness, index_store, search
    from gaius.store import init_store

    store = tmp_path / "memory"
    external = tmp_path / "external"
    external.mkdir()
    doc = external / "project.md"
    doc.write_text("projectchange searchable\n")
    first = Config(store=store, embedder="none", externals=(ExternalRoot(path=external.resolve(), project="old-project"),))
    second = Config(store=store, embedder="none", externals=(ExternalRoot(path=external.resolve(), project="new-project"),))
    init_store(first, write_user_config=False)

    index_store(store, first, rebuild=True)
    assert search(store, first, "projectchange", project="old-project")
    assert index_freshness(store, second) == (1, 0)

    index_store(store, second, rebuild=False)

    assert search(store, second, "projectchange", project="new-project")
    assert search(store, second, "projectchange", project="old-project") == []
    assert index_freshness(store, second) == (1, 1)


def test_fastembed_model_config_reaches_constructor(monkeypatch: pytest.MonkeyPatch):
    from gaius.config import Config
    from gaius.embeddings import get_embedder

    captured = {}

    class FakeFastEmbedEmbedder:
        name = "fastembed"

        def __init__(self, model_name=None):
            captured["model_name"] = model_name

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr("gaius.embeddings.FastEmbedEmbedder", FakeFastEmbedEmbedder)

    config = Config(store=Path("/tmp/memory"), embedder="fastembed", fastembed_model="BAAI/bge-small-en-v1.5")
    embedder = get_embedder(config)

    assert embedder.name == "fastembed"
    assert captured["model_name"] == "BAAI/bge-small-en-v1.5"
    assert embedder.embed(["hello"]) == [[0.1, 0.2]]

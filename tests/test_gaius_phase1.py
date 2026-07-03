from __future__ import annotations

import os
import json
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


def test_stub_and_doctor_report_required_information(tmp_path: Path):
    store = tmp_path / "memory"
    assert run_cli(store, "init").exit_code == 0

    result = run_cli(store, "stub", "agents")
    assert result.exit_code == 0, result.output
    assert "Read project state at session start" in result.output
    assert "gaius handoff" in result.output

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

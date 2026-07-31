from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import subprocess
import threading
import time

import pytest

from gaius import sync as sync_ops
from gaius.remote_operations import RemoteOperationError, RemoteOperations


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def configured_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    store = tmp_path / "memory"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "clone", str(remote), str(store)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    git(store, "config", "user.email", "remote-test@example.com")
    git(store, "config", "user.name", "Remote Test")
    (store / ".gitignore").write_text(".gaius/\n")
    (store / "global").mkdir()
    git(store, "add", "-A")
    git(store, "commit", "-m", "initialize")
    git(store, "push", "-u", "origin", "HEAD")
    branch = git(store, "branch", "--show-current")
    subprocess.run(
        ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", f"refs/heads/{branch}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return store, remote


def second_checkout(tmp_path: Path, remote: Path) -> Path:
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(remote), str(other)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    git(other, "config", "user.email", "other@example.com")
    git(other, "config", "user.name", "Other Writer")
    return other


def test_required_pull_rejects_missing_remote(tmp_path):
    store = tmp_path / "memory"
    subprocess.run(
        ["git", "init", str(store)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    with pytest.raises(sync_ops.SyncError, match="remote"):
        sync_ops.pull_required(store)


def test_commit_and_push_required_publish_changes(tmp_path):
    store, remote = configured_repo(tmp_path)
    (store / "global" / "note.md").write_text("published")

    commit_sha = sync_ops.commit_required(store, "remote write")
    sync_ops.push_required(store)

    assert commit_sha == git(store, "rev-parse", "HEAD")
    assert "remote write" in subprocess.run(
        ["git", "--git-dir", str(remote), "log", "--oneline"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def test_push_required_ignores_alternate_push_remote(tmp_path):
    store, upstream = configured_repo(tmp_path)
    alternate = tmp_path / "alternate.git"
    subprocess.run(
        ["git", "init", "--bare", str(alternate)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    git(store, "remote", "add", "alternate", str(alternate))
    branch = git(store, "branch", "--show-current")
    git(store, "config", f"branch.{branch}.pushRemote", "alternate")
    (store / "global" / "upstream.md").write_text("publish upstream")

    commit_sha = sync_ops.commit_required(store, "publish to upstream")
    sync_ops.push_required(store)

    assert (
        git(upstream, "rev-parse", f"refs/heads/{branch}")
        == commit_sha
    )
    assert subprocess.run(
        [
            "git",
            "--git-dir",
            str(alternate),
            "rev-parse",
            "--verify",
            f"refs/heads/{branch}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).returncode != 0


def test_remote_read_pulls_before_calling_reader(tmp_path):
    store, remote = configured_repo(tmp_path)
    other = second_checkout(tmp_path, remote)
    note = other / "global" / "fresh.md"
    note.parent.mkdir(exist_ok=True)
    note.write_text("fresh remote content")
    git(other, "add", "-A")
    git(other, "commit", "-m", "upstream change")
    git(other, "push")

    operations = RemoteOperations(store)
    result = operations.read(
        "read_doc",
        lambda: (store / "global" / "fresh.md").read_text(),
    )

    assert result == "fresh remote content"


def test_remote_read_refuses_unexplained_dirty_checkout(tmp_path):
    store, _ = configured_repo(tmp_path)
    (store / "global" / "unexpected.md").write_text("dirty")

    with pytest.raises(RemoteOperationError, match="dirty"):
        RemoteOperations(store).read("list_projects", lambda: [])


def test_remote_write_returns_success_only_after_push(tmp_path):
    store, remote = configured_repo(tmp_path)
    operations = RemoteOperations(store)

    result = operations.write(
        "add_memory",
        lambda: (store / "global" / "published.md").write_text("published"),
    )

    assert result["ok"] is True
    assert result["saved_local"] is True
    assert result["published"] is True
    assert not operations.pending_path.exists()
    assert "published.md" in subprocess.run(
        ["git", "--git-dir", str(remote), "show", "--stat", "--oneline", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def test_remote_write_recovers_exact_commit_after_failed_push(tmp_path):
    store, remote = configured_repo(tmp_path)
    operations = RemoteOperations(store)
    git(store, "remote", "set-url", "--push", "origin", str(tmp_path / "missing.git"))

    failed = operations.write(
        "add_memory",
        lambda: (store / "global" / "pending.md").write_text("pending"),
    )

    assert failed["ok"] is False
    assert failed["saved_local"] is True
    assert failed["published"] is False
    assert operations.pending_path.exists()

    git(store, "remote", "set-url", "--push", "origin", str(remote))
    assert operations.read("list_projects", lambda: "recovered") == "recovered"
    assert not operations.pending_path.exists()
    assert "pending.md" in subprocess.run(
        ["git", "--git-dir", str(remote), "show", "--stat", "--oneline", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def test_remote_write_reports_pull_failure_before_mutation(tmp_path):
    store, _ = configured_repo(tmp_path)
    operations = RemoteOperations(store)
    git(store, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    called = False

    def writer():
        nonlocal called
        called = True

    result = operations.write("add_memory", writer)

    assert result["ok"] is False
    assert result["saved_local"] is False
    assert result["published"] is False
    assert called is False


def test_remote_write_leaves_incomplete_marker_after_commit_failure(
    tmp_path, monkeypatch
):
    store, _ = configured_repo(tmp_path)
    operations = RemoteOperations(store)

    def fail_commit(_store, _message):
        raise sync_ops.SyncError("commit failed")

    monkeypatch.setattr(sync_ops, "commit_required", fail_commit)
    result = operations.write(
        "add_memory",
        lambda: (store / "global" / "uncommitted.md").write_text("saved"),
    )

    assert result["ok"] is False
    assert result["saved_local"] is True
    assert result["published"] is False
    assert "commit_sha" not in operations._read_pending()
    with pytest.raises(RemoteOperationError, match="incomplete write"):
        operations.read("list_projects", lambda: [])


def test_remote_operations_serialize_concurrent_readers(tmp_path):
    store, _ = configured_repo(tmp_path)
    operations = RemoteOperations(store)
    first_started = threading.Event()
    release_first = threading.Event()
    call_order = []

    def first_reader():
        call_order.append("first-start")
        first_started.set()
        assert release_first.wait(timeout=2)
        call_order.append("first-end")

    def second_reader():
        assert first_started.wait(timeout=2)
        call_order.append("second")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(operations.read, "read_doc", first_reader)
        assert first_started.wait(timeout=2)
        second = executor.submit(operations.read, "list_projects", second_reader)
        time.sleep(0.05)
        assert call_order == ["first-start"]
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert call_order == ["first-start", "first-end", "second"]


def test_remote_logs_metadata_without_result_content(tmp_path, caplog):
    store, _ = configured_repo(tmp_path)
    operations = RemoteOperations(store)
    secret = "memory-content-must-not-be-logged"

    with caplog.at_level(logging.INFO, logger="gaius.remote"):
        assert operations.read("read_doc", lambda: secret) == secret

    assert secret not in caplog.text
    assert "tool=read_doc" in caplog.text
    assert "outcome=ok" in caplog.text

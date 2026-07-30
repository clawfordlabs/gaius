from pathlib import Path
import subprocess

import pytest

from gaius import sync as sync_ops


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
    return store, remote


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

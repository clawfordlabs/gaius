from __future__ import annotations

import os
import subprocess
from pathlib import Path


class SyncError(RuntimeError):
    pass


def git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
    ):
        environment.pop(key, None)
    environment.update(
        {
            "TZ": "UTC",
            "GIT_AUTHOR_NAME": "Gaius",
            "GIT_AUTHOR_EMAIL": "gaius@local.invalid",
            "GIT_COMMITTER_NAME": "Gaius",
            "GIT_COMMITTER_EMAIL": "gaius@local.invalid",
        }
    )
    return environment


def git(store: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(store), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=git_environment(),
    )


def has_conflict(store: Path) -> bool:
    result = git(store, "status", "--porcelain", check=False)
    return any(line.startswith(("UU", "AA", "DD", "AU", "UA", "DU", "UD")) for line in result.stdout.splitlines())


def remote_exists(store: Path) -> bool:
    return bool(git(store, "remote", check=False).stdout.strip())


def current_branch(store: Path) -> str:
    branch = git(store, "branch", "--show-current", check=False).stdout.strip()
    return branch or "master"


def has_upstream(store: Path) -> bool:
    return git(store, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).returncode == 0


def sync(store: Path, message: str | None = None) -> list[str]:
    messages: list[str] = []
    if remote_exists(store):
        if has_upstream(store):
            pull = git(store, "pull", "--rebase=false", check=False)
            if pull.returncode != 0:
                if has_conflict(store):
                    raise SyncError(
                        "Git merge conflict during pull. Resolve conflicts in the memory repo, run `git add` there, "
                        "then run `git commit` or abort with `git merge --abort` before retrying `gaius sync`."
                    )
                raise SyncError((pull.stderr or pull.stdout).strip())
            messages.append("Pulled")
        else:
            messages.append("No upstream configured; skipped pull")
    else:
        messages.append("No remote configured; skipped pull")

    git(store, "add", "-A")
    status = git(store, "status", "--porcelain").stdout.strip()
    if status:
        commit_message = message or "gaius sync"
        commit = git(store, "commit", "-m", commit_message, check=False)
        if commit.returncode != 0:
            raise SyncError((commit.stderr or commit.stdout).strip())
        messages.append(f"Committed: {commit_message}")
    else:
        messages.append("No changes to commit")

    if remote_exists(store):
        push_args = ["push"] if has_upstream(store) else ["push", "-u", "origin", current_branch(store)]
        push = git(store, *push_args, check=False)
        if push.returncode != 0:
            raise SyncError((push.stderr or push.stdout).strip())
        messages.append("Pushed")
    else:
        messages.append("No remote configured; skipped push")
    return messages

from __future__ import annotations

import subprocess
from pathlib import Path


class SyncError(RuntimeError):
    pass


def git(store: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(store), *args], check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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


def require_remote_and_upstream(store: Path) -> None:
    if not remote_exists(store):
        raise SyncError("remote MCP requires a configured Git remote")
    if not has_upstream(store):
        raise SyncError("remote MCP requires a configured upstream branch")


def upstream_push_target(store: Path) -> tuple[str, str]:
    require_remote_and_upstream(store)
    branch = git(store, "branch", "--show-current", check=False).stdout.strip()
    remote = git(
        store,
        "config",
        "--get",
        f"branch.{branch}.remote",
        check=False,
    ).stdout.strip()
    merge_ref = git(
        store,
        "config",
        "--get",
        f"branch.{branch}.merge",
        check=False,
    ).stdout.strip()
    configured_remotes = git(store, "remote", check=False).stdout.splitlines()
    if remote not in configured_remotes or not merge_ref.startswith("refs/heads/"):
        raise SyncError("remote MCP upstream branch configuration is invalid")
    return remote, merge_ref


def is_clean(store: Path) -> bool:
    return not git(store, "status", "--porcelain", check=False).stdout.strip()


def is_tracked(store: Path, path: str) -> bool:
    return (
        git(
            store,
            "ls-files",
            "--error-unmatch",
            "--",
            path,
            check=False,
        ).returncode
        == 0
    )


def head_sha(store: Path) -> str:
    return git(store, "rev-parse", "HEAD").stdout.strip()


def pull_required(store: Path) -> None:
    require_remote_and_upstream(store)
    pull = git(store, "pull", "--rebase=false", check=False)
    if pull.returncode == 0:
        return
    if has_conflict(store):
        raise SyncError("Git merge conflict during remote MCP pull; resolve it manually")
    raise SyncError((pull.stderr or pull.stdout).strip())


def commit_required(store: Path, message: str) -> str:
    git(store, "add", "-A")
    if is_clean(store):
        raise SyncError("Remote MCP write produced no Git changes")
    commit = git(store, "commit", "-m", message, check=False)
    if commit.returncode != 0:
        raise SyncError((commit.stderr or commit.stdout).strip())
    return head_sha(store)


def push_required(store: Path) -> None:
    remote, merge_ref = upstream_push_target(store)
    push = git(store, "push", remote, f"HEAD:{merge_ref}", check=False)
    if push.returncode != 0:
        raise SyncError((push.stderr or push.stdout).strip())


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

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path


class SyncError(RuntimeError):
    pass


TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})"
HANDOFF_END_MARKER = "<!-- gaius-handoff-end -->"
DECISION_END_MARKER = "<!-- gaius-decision-end -->"
HANDOFF_SECTION = re.compile(
    rf"(?ms)^(?P<section>## Session Handoff - (?P<timestamp>{TIMESTAMP})\n\n.*?^{HANDOFF_END_MARKER}\n*)"
)
DECISION_ENTRY = re.compile(
    rf"(?m)^(?P<section>- (?P<timestamp>{TIMESTAMP}) - [^\n]*\n{DECISION_END_MARKER}\n*)"
)
STATE_PATH = re.compile(r"^projects/[^/]+/STATE\.md$")
DECISIONS_PATH = re.compile(r"^projects/[^/]+/DECISIONS\.md$")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def split_timestamped_entries(text: str, pattern: re.Pattern[str]) -> tuple[dict[str, str], str] | None:
    entries: dict[str, str] = {}
    for match in pattern.finditer(text):
        timestamp = match.group("timestamp")
        try:
            parse_timestamp(timestamp)
        except ValueError:
            return None
        section = match.group("section")
        if timestamp in entries:
            return None
        entries[timestamp] = section
    return entries, pattern.sub("", text)


def preserves_base_entries(base_entries: dict[str, str], candidate_entries: dict[str, str]) -> bool:
    return all(candidate_entries.get(timestamp) == section for timestamp, section in base_entries.items())


def has_colliding_additions(
    base_entries: dict[str, str], our_entries: dict[str, str], their_entries: dict[str, str]
) -> bool:
    return bool((our_entries.keys() - base_entries.keys()) & (their_entries.keys() - base_entries.keys()))


def merge_handoffs(base: str, ours: str, theirs: str) -> str | None:
    parsed = [split_timestamped_entries(text, HANDOFF_SECTION) for text in (base, ours, theirs)]
    if any(item is None for item in parsed):
        return None
    base_entries, remainder = parsed[0]
    our_entries, our_remainder = parsed[1]
    their_entries, their_remainder = parsed[2]
    if (
        remainder != our_remainder
        or remainder != their_remainder
        or not preserves_base_entries(base_entries, our_entries)
        or not preserves_base_entries(base_entries, their_entries)
        or has_colliding_additions(base_entries, our_entries, their_entries)
    ):
        return None
    entries = {**base_entries, **our_entries, **their_entries}
    if any(
        len({candidate.get(timestamp) for candidate in (base_entries, our_entries, their_entries) if timestamp in candidate}) > 1
        for timestamp in entries
    ):
        return None
    sections = "".join(entries[timestamp] for timestamp in sorted(entries, key=parse_timestamp, reverse=True))
    lines = remainder.splitlines(keepends=True)
    if lines and lines[0].startswith("# "):
        return lines[0] + "\n" + sections + "".join(lines[1:]).lstrip()
    return sections + remainder


def merge_decisions(base: str, ours: str, theirs: str) -> str | None:
    parsed = [split_timestamped_entries(text, DECISION_ENTRY) for text in (base, ours, theirs)]
    if any(item is None for item in parsed):
        return None
    base_entries, remainder = parsed[0]
    our_entries, our_remainder = parsed[1]
    their_entries, their_remainder = parsed[2]
    if (
        remainder != our_remainder
        or remainder != their_remainder
        or not preserves_base_entries(base_entries, our_entries)
        or not preserves_base_entries(base_entries, their_entries)
        or has_colliding_additions(base_entries, our_entries, their_entries)
    ):
        return None
    entries = {**base_entries, **our_entries, **their_entries}
    if any(
        len({candidate.get(timestamp) for candidate in (base_entries, our_entries, their_entries) if timestamp in candidate}) > 1
        for timestamp in entries
    ):
        return None
    return remainder.rstrip() + "\n\n" + "".join(entries[timestamp] for timestamp in sorted(entries, key=parse_timestamp))


def stage_version(store: Path, stage: int, path: str) -> str | None:
    result = git(store, "show", f":{stage}:{path}", check=False)
    return result.stdout if result.returncode == 0 else None


def resolve_concurrent_entries(store: Path) -> list[str] | None:
    paths = git(store, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    resolutions: dict[str, str] = {}
    resolved_types: list[str] = []
    for path in paths:
        base, ours, theirs = (stage_version(store, stage, path) for stage in (1, 2, 3))
        if base is None or ours is None or theirs is None:
            return None
        if STATE_PATH.fullmatch(path):
            resolved = merge_handoffs(base, ours, theirs)
            kind = "handoffs"
        elif DECISIONS_PATH.fullmatch(path):
            resolved = merge_decisions(base, ours, theirs)
            kind = "decisions"
        else:
            return None
        if resolved is None:
            return None
        resolutions[path] = resolved
        if kind not in resolved_types:
            resolved_types.append(kind)
    if not resolutions:
        return None
    for path, content in resolutions.items():
        (store / path).write_text(content)
    git(store, "add", *resolutions)
    return resolved_types


def git(store: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(store), *args], check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def has_conflict(store: Path) -> bool:
    result = git(store, "status", "--porcelain", check=False)
    return any(line.startswith(("UU", "AA", "DD", "AU", "UA", "DU", "UD")) for line in result.stdout.splitlines())


def git_path(store: Path, name: str) -> Path:
    path = Path(git(store, "rev-parse", "--git-path", name).stdout.strip())
    return path if path.is_absolute() else store / path


def has_unresolved_git_operation(store: Path) -> bool:
    if has_conflict(store):
        return True
    return any(
        git_path(store, name).exists()
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge")
    )


def remote_exists(store: Path) -> bool:
    return bool(git(store, "remote", check=False).stdout.strip())


def current_branch(store: Path) -> str:
    branch = git(store, "branch", "--show-current", check=False).stdout.strip()
    return branch or "master"


def has_upstream(store: Path) -> bool:
    return git(store, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).returncode == 0


def sync(store: Path, message: str | None = None) -> list[str]:
    messages: list[str] = []
    if has_unresolved_git_operation(store):
        raise SyncError(
            "Cannot sync during an unresolved Git operation. Resolve and commit the conflict, "
            "or abort the merge, rebase, cherry-pick, or revert before retrying `gaius sync`."
        )


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
        if has_upstream(store):
            pull = git(store, "pull", "--rebase=false", check=False)
            if pull.returncode != 0:
                if has_conflict(store):
                    resolved_types = resolve_concurrent_entries(store)
                    if resolved_types is None:
                        raise SyncError(
                            "Git merge conflict during pull. Resolve conflicts in the memory repo, run `git add` there, "
                            "then run `git commit` or abort with `git merge --abort` before retrying `gaius sync`."
                        )
                    commit = git(store, "commit", "--no-edit", check=False)
                    if commit.returncode != 0:
                        raise SyncError((commit.stderr or commit.stdout).strip())
                    messages.extend(f"Merged concurrent {kind}" for kind in resolved_types)
                else:
                    raise SyncError((pull.stderr or pull.stdout).strip())
            messages.append("Pulled")
        else:
            messages.append("No upstream configured; skipped pull")
    else:
        messages.append("No remote configured; skipped pull")

    if remote_exists(store):
        push_args = ["push"] if has_upstream(store) else ["push", "-u", "origin", current_branch(store)]
        push = git(store, *push_args, check=False)
        if push.returncode != 0:
            raise SyncError((push.stderr or push.stdout).strip())
        messages.append("Pushed")
    else:
        messages.append("No remote configured; skipped push")
    return messages

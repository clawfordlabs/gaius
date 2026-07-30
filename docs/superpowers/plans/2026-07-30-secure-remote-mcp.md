# Secure Remote MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, store-confined Gaius MCP profile that automatically
synchronizes reads and writes and is safe to connect through an authenticated
outbound tunnel.

**Architecture:** Preserve the current local MCP server as the default. Add
small validation and remote-operation modules, split Git synchronization into
reusable pull/commit/push primitives, and register a seven-tool remote profile
that excludes external roots and process execution.

**Tech Stack:** Python 3.11+, FastMCP 1.x, Click, Git subprocesses, SQLite,
pytest, standard-library `fcntl`, `threading`, and `pathlib`.

---

## File Map

- `pyproject.toml`: constrain the MCP SDK to the API Gaius currently uses.
- `src/gaius/remote_validation.py`: validate untrusted remote arguments and
  resolve store-confined documents.
- `src/gaius/sync.py`: expose required pull, commit, and push primitives while
  preserving ordinary `gaius sync`.
- `src/gaius/remote_operations.py`: serialize remote calls, manage pending
  writes, recover failed pushes, and emit metadata-only logs.
- `src/gaius/store.py`: allow project-state reads that do not create projects.
- `src/gaius/mcp_server.py`: retain the local profile and register the remote
  tool surface and annotations.
- `tests/test_package_metadata.py`: protect the supported MCP dependency range.
- `tests/test_remote_validation.py`: cover traversal, limits, confinement, and
  document resolution.
- `tests/test_remote_operations.py`: cover Git ordering, failure states,
  recovery, and serialization.
- `tests/test_remote_mcp.py`: cover profile registration, annotations, remote
  reads, writes, and filtering.
- `README.md`: document optional remote MCP and its data boundary.
- `docs/PRD.md`: replace the obsolete phase-3 bearer/Funnel sketch with the
  approved remote-profile architecture.

### Task 1: Restore Fresh-Install MCP Compatibility

**Files:**
- Create: `tests/test_package_metadata.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing dependency-range test**

```python
from pathlib import Path
import tomllib


def test_mcp_dependency_excludes_unsupported_v2():
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )

    assert "mcp>=1.0,<2" in project["project"]["dependencies"]
```

- [ ] **Step 2: Run the test and verify the current declaration fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_package_metadata.py -q
```

Expected: FAIL because the current dependency is `mcp>=1.0`.

- [ ] **Step 3: Constrain the dependency**

Change the dependency entry in `pyproject.toml`:

```toml
dependencies = [
  "click>=8.1",
  "mcp>=1.0,<2",
  "pypdf>=4.0",
  "sqlite-vec>=0.1.0",
]
```

- [ ] **Step 4: Reinstall and verify the MCP tests**

Run:

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest \
  tests/test_package_metadata.py \
  tests/test_gaius_phase1.py::test_mcp_sync_commits_and_pushes_tmp_git_repo \
  tests/test_tasks.py::test_mcp_exposes_task_tools -q
```

Expected: 3 passed, with an installed MCP version below 2.

- [ ] **Step 5: Commit the compatibility fix**

```bash
git add pyproject.toml tests/test_package_metadata.py
git commit -m "Constrain MCP to compatible SDK"
```

### Task 2: Validate And Confine Remote Inputs

**Files:**
- Create: `src/gaius/remote_validation.py`
- Create: `tests/test_remote_validation.py`

- [ ] **Step 1: Write failing segment and text-limit tests**

```python
import pytest

from gaius.remote_validation import (
    RemoteValidationError,
    validate_segment,
    validate_text,
)


@pytest.mark.parametrize(
    "value",
    ["", ".", "..", "../secret", "nested/project", r"nested\project", "bad\0name"],
)
def test_validate_segment_rejects_unsafe_values(value):
    with pytest.raises(RemoteValidationError):
        validate_segment("project", value)


def test_validate_segment_and_text_enforce_limits():
    assert validate_segment("project", "daily notes") == "daily notes"

    with pytest.raises(RemoteValidationError, match="128 characters"):
        validate_segment("project", "x" * 129)
    with pytest.raises(RemoteValidationError, match="64 bytes"):
        validate_text("memory", "x" * 65, 64)
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run:

```bash
.venv/bin/python -m pytest tests/test_remote_validation.py -q
```

Expected: collection ERROR with `ModuleNotFoundError`.

- [ ] **Step 3: Implement segment and byte-length validation**

Create `src/gaius/remote_validation.py`:

```python
from __future__ import annotations

from pathlib import Path


MAX_SEGMENT_CHARS = 128
GLOB_CHARS = frozenset("*?[]")


class RemoteValidationError(ValueError):
    pass


def validate_text(name: str, value: str, max_bytes: int) -> str:
    size = len(value.encode("utf-8"))
    if size > max_bytes:
        raise RemoteValidationError(
            f"{name} must be at most {max_bytes} bytes; received {size}"
        )
    return value


def validate_segment(name: str, value: str) -> str:
    if not value or value in {".", ".."}:
        raise RemoteValidationError(f"{name} must be one non-empty path segment")
    if len(value) > MAX_SEGMENT_CHARS:
        raise RemoteValidationError(
            f"{name} must be at most {MAX_SEGMENT_CHARS} characters"
        )
    if "\0" in value or "/" in value or "\\" in value:
        raise RemoteValidationError(f"{name} must not contain path separators")
    return value


def validate_document_reference(value: str) -> Path:
    if not value or Path(value).is_absolute() or "\0" in value:
        raise RemoteValidationError("document must be a store-relative path or ID")
    if any(character in value for character in GLOB_CHARS):
        raise RemoteValidationError("document must not contain glob characters")
    candidate = Path(value)
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise RemoteValidationError("document contains an unsafe path component")
    return candidate
```

- [ ] **Step 4: Add failing document-confinement tests**

Append to `tests/test_remote_validation.py`:

```python
from pathlib import Path

from gaius.remote_validation import resolve_store_document, store_relative_path


@pytest.mark.parametrize(
    "value",
    ["/etc/passwd", "../outside.md", "notes/*.md", "notes/[ab].md"],
)
def test_resolve_store_document_rejects_unsafe_references(tmp_path, value):
    store = tmp_path / "memory"
    store.mkdir()

    with pytest.raises(RemoteValidationError):
        resolve_store_document(store, value)


def test_resolve_store_document_accepts_relative_path_and_id(tmp_path):
    store = tmp_path / "memory"
    note = store / "projects" / "gaius" / "notes" / "memory-id.md"
    note.parent.mkdir(parents=True)
    note.write_text("complete memory")

    assert resolve_store_document(store, str(note.relative_to(store))) == note
    assert resolve_store_document(store, "memory-id") == note


def test_resolve_store_document_rejects_binary_and_oversized_files(tmp_path):
    from gaius.remote_validation import MAX_DOCUMENT_BYTES

    store = tmp_path / "memory"
    store.mkdir()
    binary = store / "artifact.pdf"
    binary.write_bytes(b"%PDF")
    large = store / "large.md"
    large.write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 1))

    with pytest.raises(RemoteValidationError, match="text memories"):
        resolve_store_document(store, "artifact.pdf")
    with pytest.raises(RemoteValidationError, match="remote limit"):
        resolve_store_document(store, "large.md")


def test_store_relative_path_rejects_absolute_external_and_escaping_symlink(tmp_path):
    store = tmp_path / "memory"
    store.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    link = store / "link.md"
    link.symlink_to(outside)

    assert store_relative_path(store, outside) is None
    assert store_relative_path(store, Path("../outside.md")) is None
    assert store_relative_path(store, Path("link.md")) is None
```

- [ ] **Step 5: Implement store confinement and document limits**

Append to `src/gaius/remote_validation.py`:

```python
REMOTE_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
MAX_DOCUMENT_BYTES = 1024 * 1024


def store_relative_path(store: Path, path: Path) -> Path | None:
    if path.is_absolute():
        return None
    root = store.resolve()
    resolved = (root / path).resolve()
    try:
        return resolved.relative_to(root)
    except ValueError:
        return None


def resolve_store_document(store: Path, value: str) -> Path:
    reference = validate_document_reference(value)
    root = store.resolve()
    candidates = [root / reference]
    if len(reference.parts) == 1:
        candidates.extend(root.rglob(reference.name))
        if not reference.suffix:
            candidates.extend(root.rglob(f"{reference.name}.md"))

    for candidate in candidates:
        relative = store_relative_path(root, candidate.relative_to(root))
        if relative is None:
            continue
        resolved = root / relative
        if not resolved.is_file():
            continue
        if resolved.suffix.lower() not in REMOTE_TEXT_SUFFIXES:
            raise RemoteValidationError("remote read_doc only serves text memories")
        if resolved.stat().st_size > MAX_DOCUMENT_BYTES:
            raise RemoteValidationError(
                f"document exceeds the {MAX_DOCUMENT_BYTES}-byte remote limit"
            )
        return resolved
    raise FileNotFoundError(f"No memory document found for {value}")
```

- [ ] **Step 6: Run validation tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_remote_validation.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit remote validation**

```bash
git add src/gaius/remote_validation.py tests/test_remote_validation.py
git commit -m "Validate remote MCP inputs"
```

### Task 3: Split Required Git Operations From Local Sync

**Files:**
- Modify: `src/gaius/sync.py`
- Create: `tests/test_remote_operations.py`

- [ ] **Step 1: Write failing required-remote tests**

Create `tests/test_remote_operations.py` with a temporary Git helper and tests:

```python
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
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    subprocess.run(["git", "clone", str(remote), str(store)], check=True)
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
    subprocess.run(["git", "init", str(store)], check=True)

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
```

- [ ] **Step 2: Run the tests and verify the new functions are absent**

Run:

```bash
.venv/bin/python -m pytest tests/test_remote_operations.py -q
```

Expected: FAIL with missing `pull_required` and `commit_required`.

- [ ] **Step 3: Add required Git primitives**

Add to `src/gaius/sync.py`:

```python
def require_remote_and_upstream(store: Path) -> None:
    if not remote_exists(store):
        raise SyncError("Remote MCP requires a configured Git remote")
    if not has_upstream(store):
        raise SyncError("Remote MCP requires a configured upstream branch")


def is_clean(store: Path) -> bool:
    return not git(store, "status", "--porcelain", check=False).stdout.strip()


def head_sha(store: Path) -> str:
    return git(store, "rev-parse", "HEAD").stdout.strip()


def pull_required(store: Path) -> None:
    require_remote_and_upstream(store)
    pull = git(store, "pull", "--rebase=false", check=False)
    if pull.returncode == 0:
        return
    if has_conflict(store):
        raise SyncError(
            "Git merge conflict during remote MCP pull; resolve it manually"
        )
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
    require_remote_and_upstream(store)
    push = git(store, "push", check=False)
    if push.returncode != 0:
        raise SyncError((push.stderr or push.stdout).strip())
```

Keep `sync()` behavior unchanged for local, offline, and remote-less stores.
Refactor its internal pull/commit/push calls only where the existing messages
and graceful-degradation semantics remain identical.

- [ ] **Step 4: Run required-operation and existing sync tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_remote_operations.py \
  tests/test_gaius_phase1.py::test_sync_happy_path_in_tmp_git_repo \
  tests/test_gaius_phase1.py::test_mcp_sync_commits_and_pushes_tmp_git_repo -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Git primitives**

```bash
git add src/gaius/sync.py tests/test_remote_operations.py
git commit -m "Add required remote Git operations"
```

### Task 4: Serialize Remote Operations And Recover Failed Pushes

**Files:**
- Create: `src/gaius/remote_operations.py`
- Modify: `tests/test_remote_operations.py`

- [ ] **Step 1: Write failing read-order and dirty-checkout tests**

Append to `tests/test_remote_operations.py`:

```python
from gaius.remote_operations import RemoteOperationError, RemoteOperations


def second_checkout(tmp_path: Path, remote: Path) -> Path:
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    git(other, "config", "user.email", "other@example.com")
    git(other, "config", "user.name", "Other Writer")
    return other


def test_remote_read_pulls_before_calling_reader(tmp_path):
    store, remote = configured_repo(tmp_path)
    other = second_checkout(tmp_path, remote)
    note = other / "global" / "fresh.md"
    note.write_text("fresh remote content")
    git(other, "add", "-A")
    git(other, "commit", "-m", "upstream change")
    git(other, "push")

    operations = RemoteOperations(store)
    result = operations.read("read_doc", lambda: (store / "global" / "fresh.md").read_text())

    assert result == "fresh remote content"


def test_remote_read_refuses_unexplained_dirty_checkout(tmp_path):
    store, _ = configured_repo(tmp_path)
    (store / "global" / "unexpected.md").write_text("dirty")

    with pytest.raises(RemoteOperationError, match="dirty"):
        RemoteOperations(store).read("list_projects", lambda: [])
```

- [ ] **Step 2: Run the two tests and verify the module is missing**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_remote_operations.py::test_remote_read_pulls_before_calling_reader \
  tests/test_remote_operations.py::test_remote_read_refuses_unexplained_dirty_checkout -q
```

Expected: collection ERROR with `ModuleNotFoundError`.

- [ ] **Step 3: Implement locking, clean preflight, and metadata-only logging**

Create `src/gaius/remote_operations.py`:

```python
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import logging
from pathlib import Path
import threading
import time
from typing import Callable, TypeVar
from uuid import uuid4

from . import sync as sync_ops


T = TypeVar("T")
LOG = logging.getLogger("gaius.remote")


class RemoteOperationError(RuntimeError):
    pass


class RemoteOperations:
    def __init__(self, store: Path):
        self.store = store
        self._thread_lock = threading.RLock()
        self.lock_path = store / ".gaius" / "remote-mcp.lock"
        self.pending_path = store / ".gaius" / "remote-pending.json"

    @contextmanager
    def _locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _log(self, request_id: str, tool: str, started: float, outcome: str) -> None:
        duration_ms = round((time.monotonic() - started) * 1000)
        LOG.info(
            "request_id=%s tool=%s duration_ms=%s outcome=%s",
            request_id,
            tool,
            duration_ms,
            outcome,
        )

    def _read_pending(self) -> dict | None:
        if not self.pending_path.exists():
            return None
        try:
            return json.loads(self.pending_path.read_text())
        except (OSError, ValueError) as exc:
            raise RemoteOperationError("Remote pending-write marker is invalid") from exc

    def _write_pending(self, pending: dict) -> None:
        temporary = self.pending_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(pending, sort_keys=True) + "\n")
        temporary.replace(self.pending_path)

    def _prepare(self) -> None:
        pending = self._read_pending()
        if pending is not None:
            commit_sha = pending.get("commit_sha")
            if not commit_sha or not sync_ops.is_clean(self.store):
                raise RemoteOperationError(
                    "Remote checkout has an incomplete write; repair it manually"
                )
            if sync_ops.head_sha(self.store) != commit_sha:
                raise RemoteOperationError(
                    "Remote pending-write marker does not match HEAD"
                )
            sync_ops.pull_required(self.store)
            pending["commit_sha"] = sync_ops.head_sha(self.store)
            self._write_pending(pending)
            sync_ops.push_required(self.store)
            self.pending_path.unlink()
            return

        if not sync_ops.is_clean(self.store):
            raise RemoteOperationError(
                "Remote checkout is dirty without a pending-write marker"
            )
        sync_ops.pull_required(self.store)

    def read(self, tool: str, reader: Callable[[], T]) -> T:
        request_id = uuid4().hex
        started = time.monotonic()
        with self._locked():
            try:
                self._prepare()
                result = reader()
            except (sync_ops.SyncError, RemoteOperationError) as exc:
                self._log(request_id, tool, started, "failed")
                raise RemoteOperationError(str(exc)) from exc
            except Exception:
                self._log(request_id, tool, started, "failed")
                raise
        self._log(request_id, tool, started, "ok")
        return result
```

- [ ] **Step 4: Run the read tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_remote_operations.py::test_remote_read_pulls_before_calling_reader \
  tests/test_remote_operations.py::test_remote_read_refuses_unexplained_dirty_checkout -q
```

Expected: 2 passed.

- [ ] **Step 5: Write failing write and failed-push recovery tests**

Append to `tests/test_remote_operations.py`:

```python
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
```

- [ ] **Step 6: Implement structured remote writes**

Add to `RemoteOperations`:

```python
    def write(self, tool: str, writer: Callable[[], T]) -> dict:
        request_id = uuid4().hex
        started = time.monotonic()
        with self._locked():
            try:
                self._prepare()
            except (sync_ops.SyncError, RemoteOperationError) as exc:
                self._log(request_id, tool, started, "preflight_failed")
                return {
                    "ok": False,
                    "saved_local": False,
                    "published": False,
                    "error": str(exc),
                }

            pending = {"request_id": request_id, "tool": tool}
            self._write_pending(pending)
            try:
                value = writer()
                commit_sha = sync_ops.commit_required(
                    self.store, f"gaius remote: {tool}"
                )
            except Exception as exc:
                saved_local = not sync_ops.is_clean(self.store)
                if not saved_local:
                    self.pending_path.unlink(missing_ok=True)
                self._log(request_id, tool, started, "write_failed")
                return {
                    "ok": False,
                    "saved_local": saved_local,
                    "published": False,
                    "error": str(exc),
                }

            pending["commit_sha"] = commit_sha
            self._write_pending(pending)
            try:
                sync_ops.push_required(self.store)
            except sync_ops.SyncError as exc:
                self._log(request_id, tool, started, "push_failed")
                return {
                    "ok": False,
                    "saved_local": True,
                    "published": False,
                    "error": str(exc),
                }

            self.pending_path.unlink()
            self._log(request_id, tool, started, "ok")
            return {
                "ok": True,
                "saved_local": True,
                "published": True,
                "value": value,
            }
```

- [ ] **Step 7: Add and pass serialization and log-redaction tests**

Append to `tests/test_remote_operations.py`:

```python
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time


def test_remote_operations_serialize_concurrent_readers(tmp_path):
    store, _ = configured_repo(tmp_path)
    operations = RemoteOperations(store)
    counter_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def reader():
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with counter_lock:
            active -= 1
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: operations.read("read_doc", reader),
                range(2),
            )
        )

    assert results == ["ok", "ok"]
    assert maximum_active == 1


def test_remote_logs_metadata_without_result_content(tmp_path, caplog):
    store, _ = configured_repo(tmp_path)
    operations = RemoteOperations(store)

    with caplog.at_level(logging.INFO, logger="gaius.remote"):
        result = operations.read("read_doc", lambda: "SENSITIVE_RESULT")

    assert result == "SENSITIVE_RESULT"
    assert "tool=read_doc" in caplog.text
    assert "outcome=ok" in caplog.text
    assert "SENSITIVE_RESULT" not in caplog.text
```

Run:

```bash
.venv/bin/python -m pytest tests/test_remote_operations.py -q
```

Expected: all remote-operation tests pass.

- [ ] **Step 8: Commit remote operation coordination**

```bash
git add src/gaius/remote_operations.py tests/test_remote_operations.py
git commit -m "Serialize remote memory operations"
```

### Task 5: Make Project-State Reads Pure

**Files:**
- Modify: `src/gaius/store.py`
- Modify: `tests/test_gaius_phase1.py`

- [ ] **Step 1: Write a failing non-creating state test**

Add to `tests/test_gaius_phase1.py`:

```python
def test_project_state_can_refuse_to_create_missing_project(tmp_path):
    from gaius.config import Config
    from gaius.store import project_state

    store = tmp_path / "memory"
    store.mkdir()
    (store / "projects").mkdir()
    config = Config(store=store, embedder="none")

    with pytest.raises(FileNotFoundError, match="missing"):
        project_state(config, "missing", create=False)

    assert not (store / "projects" / "missing").exists()
```

- [ ] **Step 2: Run the test and verify the signature fails**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_gaius_phase1.py::test_project_state_can_refuse_to_create_missing_project -q
```

Expected: FAIL because `project_state` has no `create` argument.

- [ ] **Step 3: Add the pure-read option**

Change `project_state` in `src/gaius/store.py`:

```python
def project_state(
    config: Config,
    project: str,
    decisions: int = 10,
    *,
    create: bool = True,
) -> str:
    store = ensure_store(config)
    project_dir = store / "projects" / project
    if create:
        project_dir = ensure_project(store, project)
    elif not project_dir.is_dir():
        raise FileNotFoundError(f"No Gaius project found for {project}")
    text = (project_dir / "STATE.md").read_text()
    decision_lines = [
        line
        for line in (project_dir / "DECISIONS.md").read_text().splitlines()
        if line.startswith("- ")
    ]
    if decision_lines:
        text += (
            "\n\n## Recent Decisions\n\n"
            + "\n".join(decision_lines[-decisions:])
            + "\n"
        )
    return text
```

The default remains `create=True`, preserving local CLI and MCP behavior.

- [ ] **Step 4: Run store and CLI state tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_gaius_phase1.py::test_project_state_can_refuse_to_create_missing_project \
  tests/test_gaius_phase1.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit pure project reads**

```bash
git add src/gaius/store.py tests/test_gaius_phase1.py
git commit -m "Allow non-creating project state reads"
```

### Task 6: Register The Remote MCP Profile

**Files:**
- Modify: `src/gaius/mcp_server.py`
- Create: `tests/test_remote_mcp.py`
- Modify: `tests/test_tasks.py`

- [ ] **Step 1: Write failing exact-profile and annotation tests**

Create `tests/test_remote_mcp.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify `build_server` rejects the profile**

Run:

```bash
.venv/bin/python -m pytest tests/test_remote_mcp.py -q
```

Expected: FAIL because `build_server` accepts no profile.

- [ ] **Step 3: Split local and remote server construction**

Refactor `src/gaius/mcp_server.py` around these interfaces:

```python
from argparse import ArgumentParser
from dataclasses import replace
from pathlib import Path

from mcp.types import ToolAnnotations

from .remote_operations import RemoteOperations
from .remote_validation import (
    RemoteValidationError,
    validate_document_reference,
    resolve_store_document,
    store_relative_path,
    validate_segment,
    validate_text,
)


READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def build_server(profile: str = "local"):
    if profile == "local":
        return build_local_server()
    if profile == "remote":
        return build_remote_server()
    raise ValueError(f"Unknown MCP profile: {profile}")
```

Move the current ten tools unchanged into `build_local_server()`.

In `build_remote_server()`, load one service-lifetime configuration and remove
external roots and task execution:

```python
config = replace(load_config(), externals=(), task_command=())
operations = RemoteOperations(config.store)
mcp = FastMCP("gaius")
```

Register only the seven approved tools. Validate arguments before entering the
Git operation lock. Use `operations.read()` for reads and
`operations.write()` for writes.

- [ ] **Step 4: Implement the confined remote read tools**

Use these bodies in `build_remote_server()`:

```python
@mcp.tool(annotations=READ_ANNOTATIONS)
def search_memory(
    query: str, project: str | None = None, limit: int = 10
) -> list[dict]:
    validate_text("query", query, 4 * 1024)
    if project is not None:
        validate_segment("project", project)
    if not 1 <= limit <= 50:
        raise RemoteValidationError("limit must be between 1 and 50")

    def run_search():
        output = []
        for result in search(config.store, config, query, project, limit):
            relative = store_relative_path(config.store, result.path)
            if relative is None:
                continue
            output.append(
                {
                    "path": relative.as_posix(),
                    "title": result.title,
                    "heading": result.heading,
                    "snippet": result.snippet,
                    "score": result.score,
                }
            )
        return output

    return operations.read("search_memory", run_search)


@mcp.tool(annotations=READ_ANNOTATIONS)
def get_project_state(project: str) -> str:
    validate_segment("project", project)
    return operations.read(
        "get_project_state",
        lambda: store_ops.project_state(config, project, create=False),
    )


@mcp.tool(annotations=READ_ANNOTATIONS)
def list_projects() -> list[str]:
    return operations.read(
        "list_projects",
        lambda: [name for name, _ in store_ops.list_projects(config)],
    )


@mcp.tool(annotations=READ_ANNOTATIONS)
def read_doc(path: str) -> str:
    validate_document_reference(path)
    return operations.read(
        "read_doc",
        lambda: resolve_store_document(config.store, path).read_text(
            errors="replace"
        ),
    )
```

The standalone `validate_document_reference()` call rejects unsafe input before
Git access. Full resolution remains inside the locked reader so a newly
synchronized document can be found.

- [ ] **Step 5: Implement validated write tools**

Use store-relative result values:

```python
def relative_value(path: Path) -> str:
    return path.resolve().relative_to(config.store.resolve()).as_posix()


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def add_memory(
    text: str,
    tags: list[str] | None = None,
    topic: str | None = None,
    project: str | None = None,
) -> dict:
    validate_text("memory", text, 64 * 1024)
    clean_tags = tags or []
    if len(clean_tags) > 32:
        raise RemoteValidationError("tags must contain at most 32 values")
    for tag in clean_tags:
        validate_segment("tag", tag)
    if topic is not None:
        validate_segment("topic", topic)
    if project is not None:
        validate_segment("project", project)
    return operations.write(
        "add_memory",
        lambda: relative_value(
            store_ops.add_memory(config, text, clean_tags, topic, project)
        ),
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def handoff(project: str, summary: str) -> dict:
    validate_segment("project", project)
    validate_text("handoff", summary, 64 * 1024)
    return operations.write(
        "handoff",
        lambda: relative_value(store_ops.handoff(config, project, summary)),
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def log_decision(project: str, text: str) -> dict:
    validate_segment("project", project)
    validate_text("decision", text, 16 * 1024)
    return operations.write(
        "log_decision",
        lambda: relative_value(store_ops.decide(config, project, text)),
    )
```

- [ ] **Step 6: Add CLI profile parsing**

Replace `main()` with:

```python
def main() -> None:
    parser = ArgumentParser(prog="gaius-mcp")
    parser.add_argument(
        "--profile",
        choices=("local", "remote"),
        default="local",
    )
    args = parser.parse_args()
    build_server(args.profile).run()
```

- [ ] **Step 7: Run tool-surface tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_remote_mcp.py \
  tests/test_tasks.py::test_mcp_exposes_task_tools \
  tests/test_gaius_phase1.py::test_mcp_sync_commits_and_pushes_tmp_git_repo -q
```

Expected: all tests pass and the local tool surface remains unchanged.

- [ ] **Step 8: Commit MCP profile registration**

```bash
git add src/gaius/mcp_server.py tests/test_remote_mcp.py tests/test_tasks.py
git commit -m "Add secure remote MCP profile"
```

### Task 7: Verify Remote MCP End To End

**Files:**
- Modify: `tests/test_remote_mcp.py`

- [ ] **Step 1: Add a real-Git remote MCP fixture**

Add to `tests/test_remote_mcp.py`:

```python
from pathlib import Path
import subprocess

import pytest


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
    subprocess.run(["git", "init", "--bare", str(bare_remote)], check=True)
    subprocess.run(["git", "clone", str(bare_remote), str(store)], check=True)
    run_git(store, "config", "user.email", "remote-mcp@example.com")
    run_git(store, "config", "user.name", "Remote MCP")
    (store / ".gitignore").write_text(".gaius/\n")
    (store / "global").mkdir()
    (store / "projects").mkdir()
    run_git(store, "add", "-A")
    run_git(store, "commit", "-m", "initialize")
    run_git(store, "push", "-u", "origin", "HEAD")

    monkeypatch.setenv("GAIUS_MEMORY_DIR", str(store))
    monkeypatch.setenv("GAIUS_EMBEDDER", "none")
    server_tools = tools(build_server("remote"))
    return {
        "tools": server_tools,
        "store": store,
        "remote": bare_remote,
        "root": tmp_path,
    }
```

- [ ] **Step 2: Write a failing automatic-write integration test**

```python
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
```

- [ ] **Step 3: Write read freshness and state-purity tests**

Append to `tests/test_remote_mcp.py`:

```python
def push_fresh_content(remote_mcp) -> None:
    other = remote_mcp["root"] / "other"
    subprocess.run(
        ["git", "clone", str(remote_mcp["remote"]), str(other)],
        check=True,
    )
    run_git(other, "config", "user.email", "other@example.com")
    run_git(other, "config", "user.name", "Other Writer")
    note = other / "global" / "fresh.md"
    note.write_text("# Fresh\n\nfreshneedle from another host\n")
    project = other / "projects" / "synced"
    project.mkdir()
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


def test_remote_project_state_does_not_create_missing_project(remote_mcp):
    store = remote_mcp["store"]

    with pytest.raises(FileNotFoundError):
        remote_mcp["tools"]["get_project_state"].fn("missing")

    assert not (store / "projects" / "missing").exists()
```

- [ ] **Step 4: Write external-result and traversal defense tests**

Append to `tests/test_remote_mcp.py`:

```python
from gaius.indexer import SearchResult
from gaius.remote_validation import RemoteValidationError


def test_remote_search_filters_external_results(remote_mcp, monkeypatch):
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
        ("add_memory", ("text", ["../escape"], None, None)),
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
```

- [ ] **Step 5: Run all remote tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_remote_validation.py \
  tests/test_remote_operations.py \
  tests/test_remote_mcp.py -q
```

Expected: all remote tests pass.

- [ ] **Step 6: Commit end-to-end coverage**

```bash
git add tests/test_remote_mcp.py
git commit -m "Test remote MCP end to end"
```

### Task 8: Document The Opt-In Remote Boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/PRD.md`

- [ ] **Step 1: Update the PRD phase and security posture**

Replace the phase-3 bearer-token/Tailscale-Funnel sketch with:

```markdown
**Phase 3 (remote surfaces):** `gaius-mcp --profile remote` exposes a reduced,
store-confined tool set with automatic synchronization. A dedicated service
account runs the stdio server behind an authenticated outbound tunnel for
private hosted-agent access. Public HTTPS/OAuth transport is a later,
vendor-neutral gateway; remote task execution remains separately gated.
```

Add these security bullets:

```markdown
- Remote MCP is explicit opt-in; the default installation remains local-only.
- Memory returned through remote MCP enters the hosted provider's data boundary.
- The remote profile excludes external roots and delegated task execution.
- A successful remote write means its Git commit was pushed to the configured
  upstream.
```

- [ ] **Step 2: Add a generic README remote section**

Add this README section after the local MCP registration instructions:

````markdown
### Remote MCP (opt-in)

The default `gaius-mcp` profile is local and uses stdio. Hosted agent surfaces
can use the reduced remote profile:

```bash
gaius-mcp --profile remote
```

The remote profile exposes `search_memory`, `get_project_state`,
`list_projects`, `read_doc`, `add_memory`, `handoff`, and `log_decision`.
It does not expose sync controls, external roots, or task execution. Reads pull
before returning. Writes pull first and report success only after committing
and pushing the change.

Run remote MCP under a dedicated operating-system account with its own memory
checkout, index, virtual environment, and git-only credential. Do not configure
external roots or `task_command` for that account. Put the stdio server behind
an authenticated outbound tunnel and store its runtime credential outside Git
with owner-only permissions.

Remote access is explicit opt-in. Memory returned to a hosted agent leaves the
user's machines and enters that provider's data boundary. Review the provider's
current tunnel, retention, and workspace-access documentation before enabling
it.
````

Do not add personal hostnames, account names, keys, tunnel IDs, or service
files. Do not make `setup` provision remote access automatically.

- [ ] **Step 3: Check documentation formatting and stale claims**

Run:

```bash
rg -n "bearer token first|Tailscale Funnel|run_task.*remote-work primitive" \
  README.md docs/PRD.md
awk 'length($0) > 110 { print FNR \":\" length($0) \":\" $0 }' \
  README.md docs/PRD.md
git diff --check
```

Expected: no stale phase-3 claims, no lines over 110 characters, and no
whitespace errors.

- [ ] **Step 4: Commit remote documentation**

```bash
git add README.md docs/PRD.md
git commit -m "Document secure remote MCP operation"
```

### Task 9: Full Verification And Pull Request

**Files:**
- Verify all changed files

- [ ] **Step 1: Run the complete test suite**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Verify installed dependency and profile help**

Run:

```bash
.venv/bin/python -c \
  "import importlib.metadata as m; print(m.version('mcp'))"
.venv/bin/gaius-mcp --help
```

Expected: MCP reports a 1.x version and help lists
`--profile {local,remote}`.

- [ ] **Step 3: Audit the final diff for boundary regressions**

Run:

```bash
git diff origin/main...HEAD --check
git diff origin/main...HEAD -- \
  src/gaius/remote_validation.py \
  src/gaius/remote_operations.py \
  src/gaius/mcp_server.py \
  src/gaius/sync.py
rg -n "run_task|task_status|externals|read_doc|absolute|remote" \
  src/gaius/mcp_server.py src/gaius/remote_validation.py
```

Confirm:

- the remote profile registers exactly seven tools;
- local MCP still registers all ten tools;
- no remote tool accepts an absolute path;
- remote configuration clears external roots and task commands;
- write success follows push success;
- logs contain no arguments or results; and
- no deployment secret or personal setup detail is in the repository.

- [ ] **Step 4: Verify branch state and commit any final test-only correction**

Run:

```bash
git status --short --branch
git log --oneline --decorate origin/main..HEAD
```

Expected: a clean feature branch containing the design and focused
implementation commits.

- [ ] **Step 5: Push and open a pull request**

```bash
git push -u origin feature/remote-mcp
gh pr create \
  --base main \
  --head feature/remote-mcp \
  --title "Add secure remote MCP profile" \
  --body "Adds a store-confined remote MCP profile with automatic Git sync,
write recovery, MCP annotations, and no external-root or task-runner access."
```

- [ ] **Step 6: Record the private deployment checkpoint**

After CI and review are clean, stop code work and perform the private Mac
deployment interactively. Keep operating-system account details, SSH
restrictions, tunnel identifiers, runtime credentials, and service definitions
out of Git. Verify on the target machine:

```text
1. Dedicated service account has its own checkout and cannot read other homes.
2. gaius-mcp --profile remote completes an MCP initialization handshake.
3. The outbound tunnel reports healthy and reconnects after restart.
4. Hosted search and read_doc return store-only content.
5. Hosted add_memory returns published=true.
6. A separate Gaius host can sync and retrieve that memory.
7. Stopping the service and revoking the runtime key disables access.
```

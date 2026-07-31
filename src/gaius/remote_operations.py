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

    def write(
        self,
        tool: str,
        writer: Callable[[], T],
        *,
        noop_is_success: Callable[[T], bool] | None = None,
    ) -> dict:
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
                if (
                    noop_is_success is not None
                    and sync_ops.is_clean(self.store)
                    and noop_is_success(value)
                ):
                    self.pending_path.unlink()
                    self._log(request_id, tool, started, "already_published")
                    return {
                        "ok": True,
                        "saved_local": True,
                        "published": True,
                        "value": value,
                    }
                commit_sha = sync_ops.commit_required(
                    self.store,
                    f"gaius remote: {tool}",
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

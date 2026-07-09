from __future__ import annotations

import json
import os
import random
import string
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .indexer import index_files
from .store import ensure_project, ensure_store
from .utils import now_iso


class TaskError(RuntimeError):
    pass


def tasks_dir(store: Path) -> Path:
    path = store / ".gaius" / "tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_paths(store: Path, task_id: str) -> tuple[Path, Path, Path]:
    root = tasks_dir(store)
    return root / f"{task_id}.json", root / f"{task_id}.log", root / f"{task_id}.exit"


def generate_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"{stamp}-{suffix}"


def setup_instructions() -> str:
    return (
        "task_command is not configured. Set it in ~/.config/gaius/config.toml, for example:\n"
        'task_command = ["codex", "exec", "--sandbox", "workspace-write"]\n'
        'or task_command = ["claude", "-p"]'
    )


def read_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise TaskError(f"Unknown task id: {path.stem}")
    return json.loads(path.read_text())


def write_meta(path: Path, meta: dict[str, Any]) -> None:
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")


def tail_lines(path: Path, count: int = 50) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(errors="replace").splitlines()[-count:]


def run_task(config: Config, project: str, prompt: str) -> str:
    if not config.task_command:
        raise TaskError(setup_instructions())
    store = ensure_store(config)
    project_dir = ensure_project(store, project)
    task_id = generate_task_id()
    meta_path, log_path, exit_path = task_paths(store, task_id)
    command = [*config.task_command, prompt]
    started = now_iso()
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "gaius.task_worker", str(exit_path), *command],
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write_meta(
        meta_path,
        {
            "task_id": task_id,
            "project": project,
            "prompt": prompt,
            "pid": process.pid,
            "started": started,
            "status": "running",
            "log_path": str(log_path),
            "command": command,
        },
    )
    return task_id


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def result_path_for(store: Path, project: str, task_id: str) -> Path:
    return store / "projects" / project / "notes" / "tasks" / f"{task_id}.md"


def finalize_task(config: Config, meta_path: Path, log_path: Path, exit_path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    if meta.get("finalized"):
        return meta
    try:
        exit_code = int(exit_path.read_text().strip())
    except ValueError:
        exit_code = 127
    status = "completed" if exit_code == 0 else "failed"
    finished = now_iso()
    store = config.store
    result_path = result_path_for(store, meta["project"], meta["task_id"])
    result_path.parent.mkdir(parents=True, exist_ok=True)
    log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
    result_path.write_text(
        "---\n"
        f"task_id: {meta['task_id']}\n"
        f"prompt: {json.dumps(meta['prompt'])}\n"
        f"started: {meta['started']}\n"
        f"finished: {finished}\n"
        f"exit_code: {exit_code}\n"
        "---\n\n"
        "# Delegated Task Output\n\n"
        "```text\n"
        f"{log_text.rstrip()}\n"
        "```\n"
    )
    meta.update(
        {
            "status": status,
            "exit_code": exit_code,
            "finished": finished,
            "finalized": True,
            "result_path": str(result_path),
        }
    )
    write_meta(meta_path, meta)
    index_files(store, config, [result_path])
    return meta


def task_status(config: Config, task_id: str) -> dict[str, Any]:
    store = ensure_store(config)
    meta_path, log_path, exit_path = task_paths(store, task_id)
    meta = read_meta(meta_path)
    if exit_path.exists():
        meta = finalize_task(config, meta_path, log_path, exit_path, meta)
    elif not process_exists(int(meta["pid"])):
        meta.update({"status": "failed", "exit_code": None, "finished": now_iso(), "finalized": True})
        write_meta(meta_path, meta)
    response = {
        "task_id": task_id,
        "project": meta["project"],
        "prompt": meta["prompt"],
        "status": meta["status"],
        "pid": meta["pid"],
        "started": meta["started"],
        "log_path": str(log_path),
        "log_tail": tail_lines(log_path),
    }
    if "exit_code" in meta:
        response["exit_code"] = meta["exit_code"]
    if "finished" in meta:
        response["finished"] = meta["finished"]
    if "result_path" in meta:
        response["result_path"] = meta["result_path"]
    return response


def list_tasks(config: Config) -> list[dict[str, Any]]:
    store = ensure_store(config)
    items: list[dict[str, Any]] = []
    for path in tasks_dir(store).glob("*.json"):
        try:
            items.append(task_status(config, path.stem))
        except TaskError:
            continue
    return sorted(items, key=lambda item: item["started"], reverse=True)

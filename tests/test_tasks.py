from __future__ import annotations

import os
import sys
import time
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from gaius.config import Config, load_config
from gaius.indexer import search
from gaius.store import init_store


@dataclass
class CliResult:
    exit_code: int
    output: str


def run_cli(store: Path, *args: str, task_command: str | None = None) -> CliResult:
    env = {**os.environ, "GAIUS_MEMORY_DIR": str(store), "GAIUS_EMBEDDER": "none"}
    if task_command:
        env["GAIUS_TASK_COMMAND"] = task_command
    gaius = Path(sys.executable).with_name("gaius")
    result = subprocess.run([str(gaius), *args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    return CliResult(result.returncode, result.stdout)


def make_task_script(path: Path) -> Path:
    script = path / "task_script.py"
    script.write_text(
        "import os, sys\n"
        "print('PROMPT=' + sys.argv[-1])\n"
        "print('CWD=' + os.getcwd())\n"
    )
    return script


def wait_for_done(config: Config, task_id: str, deadline: float = 3.0) -> dict:
    from gaius.tasks import task_status

    end = time.monotonic() + deadline
    last = {}
    while time.monotonic() < end:
        last = task_status(config, task_id)
        if last["status"] != "running":
            return last
        time.sleep(0.05)
    pytest.fail(f"task did not finish before deadline; last status={last}")


def test_run_task_completes_finalizes_indexes_and_uses_project_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.tasks import run_task

    store = tmp_path / "memory"
    script = make_task_script(tmp_path)
    monkeypatch.setenv("GAIUS_MEMORY_DIR", str(store))
    monkeypatch.setenv("GAIUS_TASK_COMMAND", f"{sys.executable} {script}")
    config = load_config()
    init_store(config, write_user_config=False)

    task_id = run_task(config, "gaius", "transcribe artifact alpha")
    status = wait_for_done(config, task_id)

    project_dir = store / "projects" / "gaius"
    result_file = project_dir / "notes" / "tasks" / f"{task_id}.md"
    assert status["status"] == "completed"
    assert status["exit_code"] == 0
    assert "PROMPT=transcribe artifact alpha" in "\n".join(status["log_tail"])
    assert f"CWD={project_dir}" in result_file.read_text()
    assert result_file.exists()

    results = search(store, config, "transcribe alpha", project="gaius")
    assert any(result.path == Path("projects/gaius/notes/tasks") / f"{task_id}.md" for result in results)

    status_again = wait_for_done(config, task_id)
    assert status_again["status"] == "completed"
    assert result_file.read_text().count("PROMPT=transcribe artifact alpha") == 1


def test_task_status_unknown_id_errors_clearly(tmp_path: Path):
    from gaius.tasks import TaskError, task_status

    config = Config(store=tmp_path / "memory")
    init_store(config, write_user_config=False)
    with pytest.raises(TaskError, match="Unknown task id"):
        task_status(config, "missing-task")


def test_run_task_without_command_errors_with_setup_instructions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gaius.tasks import TaskError, run_task

    monkeypatch.delenv("GAIUS_TASK_COMMAND", raising=False)
    config = Config(store=tmp_path / "memory")
    init_store(config, write_user_config=False)
    with pytest.raises(TaskError, match="task_command"):
        run_task(config, "gaius", "do work")


def test_task_command_loads_from_config_and_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("GAIUS_TASK_COMMAND", raising=False)
    config_file = tmp_path / "config" / "gaius" / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        f'store_path = "{tmp_path / "memory"}"\n'
        'task_command = ["codex", "exec", "--sandbox", "workspace-write"]\n'
    )

    config = load_config()
    assert config.task_command == ("codex", "exec", "--sandbox", "workspace-write")

    monkeypatch.setenv("GAIUS_TASK_COMMAND", 'claude -p --model "Opus Test"')
    config = load_config()
    assert config.task_command == ("claude", "-p", "--model", "Opus Test")


def test_task_cli_start_status_list_and_doctor_reports_command(tmp_path: Path):
    store = tmp_path / "memory"
    script = make_task_script(tmp_path)
    task_command = f"{sys.executable} {script}"
    assert run_cli(store, "init", task_command=task_command).exit_code == 0

    result = run_cli(store, "task", "gaius", "cli prompt beta", task_command=task_command)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    task_id = next(line.removeprefix("Task: ").strip() for line in lines if line.startswith("Task: "))
    assert task_id

    deadline = time.monotonic() + 3
    status_output = ""
    while time.monotonic() < deadline:
        status = run_cli(store, "task", "--status", task_id, task_command=task_command)
        assert status.exit_code == 0, status.output
        status_output = status.output
        if "Status: completed" in status_output:
            break
        time.sleep(0.05)
    assert "Status: completed" in status_output
    assert "PROMPT=cli prompt beta" in status_output

    listed = run_cli(store, "task", "--list", task_command=task_command)
    assert listed.exit_code == 0, listed.output
    assert task_id in listed.output
    assert "completed" in listed.output

    doctor = run_cli(store, "doctor", task_command=task_command)
    assert doctor.exit_code == 0, doctor.output
    assert f"Task command: {sys.executable} {script}" in doctor.output


def test_mcp_exposes_task_tools():
    from gaius.mcp_server import build_server

    server = build_server()
    tools = getattr(getattr(server, "_tool_manager"), "_tools")
    assert "sync" in tools
    assert "run_task" in tools
    assert "task_status" in tools

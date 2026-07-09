from __future__ import annotations

import os
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExternalRoot:
    path: Path
    project: str | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    store: Path
    embedder: str = "auto"
    externals: tuple[ExternalRoot, ...] = ()
    index_max_file_mb: float = 10.0
    fastembed_model: str | None = None
    openai_base_url: str | None = None
    openai_key_env: str | None = None
    openai_model: str | None = None
    task_command: tuple[str, ...] = ()


def config_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "gaius" / "config.toml"


def load_raw_config() -> dict:
    path = config_file()
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text())


def load_config(explicit_store: str | Path | None = None) -> Config:
    raw = load_raw_config()
    env_store = os.environ.get("GAIUS_MEMORY_DIR")
    store_value = explicit_store or env_store or raw.get("store_path") or "~/memory"
    embedder = os.environ.get("GAIUS_EMBEDDER") or raw.get("embedder") or "auto"
    env_task_command = os.environ.get("GAIUS_TASK_COMMAND")
    task_command = shlex.split(env_task_command) if env_task_command else raw.get("task_command") or []
    externals = [
        ExternalRoot(
            path=Path(item["path"]).expanduser().resolve(),
            project=item.get("project"),
            include=tuple(str(pattern) for pattern in item.get("include", ())),
            exclude=tuple(str(pattern) for pattern in item.get("exclude", ())),
        )
        for item in raw.get("external", ())
        if item.get("path")
    ]
    externals.extend(
        ExternalRoot(path=Path(vault).expanduser().resolve())
        for vault in raw.get("vault_paths", ())
    )
    fastembed = raw.get("fastembed") or {}
    openai = raw.get("openai") or {}
    return Config(
        store=Path(store_value).expanduser().resolve(),
        embedder=embedder,
        externals=tuple(externals),
        index_max_file_mb=float(raw.get("index_max_file_mb", 10)),
        fastembed_model=fastembed.get("model"),
        openai_base_url=openai.get("base_url"),
        openai_key_env=openai.get("key_env"),
        openai_model=openai.get("model"),
        task_command=tuple(str(part) for part in task_command),
    )


def write_config(store: Path) -> Path:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    store_line = f'store_path = "{store.expanduser().resolve()}"'
    if not path.exists():
        path.write_text(store_line + "\n")
        return path

    lines = path.read_text().splitlines()
    replaced = False
    output: list[str] = []
    for line in lines:
        if not replaced and line.strip().startswith("store_path") and "=" in line and not line.startswith("["):
            output.append(store_line)
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.insert(0, store_line)
    path.write_text("\n".join(output) + "\n")
    return path

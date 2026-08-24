from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config, write_config
from .indexer import index_files
from .utils import now_iso, short_hash, slugify, title_for_project, today


class DecisionError(ValueError):
    pass


def init_store(config: Config, write_user_config: bool = True) -> Path:
    store = config.store
    for subdir in ["global", "projects", ".gaius"]:
        (store / subdir).mkdir(parents=True, exist_ok=True)
    (store / ".gitignore").write_text(".gaius/\n")
    (store / ".gitattributes").write_text("*.md text eol=lf\n")
    if not (store / ".git").exists():
        subprocess.run(["git", "init"], cwd=store, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if write_user_config:
        write_config(store)
    return store


def ensure_store(config: Config) -> Path:
    if not config.store.exists():
        raise FileNotFoundError(f"Memory store does not exist: {config.store}. Run `gaius init` first.")
    return config.store


def ensure_project(store: Path, project: str) -> Path:
    project_dir = store / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    state = project_dir / "STATE.md"
    if not state.exists():
        state.write_text(
            f"# {title_for_project(project)}\n\n"
            "## Current Status\n\nNot yet recorded.\n\n"
            "## Next Steps\n\n- Update this handoff file.\n\n"
            "## Open Blockers\n\nNone recorded.\n"
        )
    decisions = project_dir / "DECISIONS.md"
    if not decisions.exists():
        decisions.write_text(f"# {title_for_project(project)} Decisions\n\n")
    (project_dir / "notes").mkdir(exist_ok=True)
    (project_dir / "artifacts").mkdir(exist_ok=True)
    return project_dir


def add_memory(config: Config, text: str, tags: list[str] | None = None, topic: str | None = None, project: str | None = None) -> Path:
    store = ensure_store(config)
    tags = tags or []
    memory_id = f"{today()}-{slugify(text)}-{short_hash(text)}"
    if project:
        base = ensure_project(store, project) / "notes"
    else:
        base = store / "global" / (topic or "general")
        base.mkdir(parents=True, exist_ok=True)
    path = base / f"{memory_id}.md"
    tag_text = ", ".join(tags)
    path.write_text(
        "---\n"
        f"id: {memory_id}\n"
        f"tags: [{tag_text}]\n"
        "source: gaius\n"
        f"created: {today()}\n"
        "---\n"
        f"{text.rstrip()}\n"
    )
    index_files(store, config, [path])
    return path


def read_doc(config: Config, id_or_path: str) -> str:
    store = ensure_store(config)
    candidate = Path(id_or_path).expanduser()
    paths: list[Path] = []
    if candidate.is_absolute():
        paths.append(candidate)
    else:
        paths.append(store / candidate)
        paths.extend(store.rglob(id_or_path))
        if not id_or_path.endswith(".md"):
            paths.extend(store.rglob(f"{id_or_path}.md"))
    for path in paths:
        if path.is_file():
            return path.read_text(errors="replace")
    raise FileNotFoundError(f"No memory document found for {id_or_path}")


def project_state(config: Config, project: str, decisions: int = 10) -> str:
    store = ensure_store(config)
    project_dir = ensure_project(store, project)
    text = (project_dir / "STATE.md").read_text()
    decision_lines = [line for line in (project_dir / "DECISIONS.md").read_text().splitlines() if line.startswith("- ")]
    if decision_lines:
        text += "\n\n## Recent Decisions\n\n" + "\n".join(decision_lines[-decisions:]) + "\n"
    return text


def handoff(config: Config, project: str, summary: str) -> Path:
    store = ensure_store(config)
    project_dir = ensure_project(store, project)
    state = project_dir / "STATE.md"
    existing = state.read_text()
    section = f"## Session Handoff - {now_iso()}\n\n{summary.rstrip()}\n\n"
    lines = existing.splitlines(keepends=True)
    if lines and lines[0].startswith("# "):
        new_text = lines[0] + "\n" + section + "".join(lines[1:]).lstrip()
    else:
        new_text = section + existing
    state.write_text(new_text)
    index_files(store, config, [state])
    return state


def decide(config: Config, project: str, text: str) -> Path:
    if "\n" in text.rstrip("\r\n") or "\r" in text.rstrip("\r\n"):
        raise DecisionError("Decision text must be a single line.")
    store = ensure_store(config)
    project_dir = ensure_project(store, project)
    path = project_dir / "DECISIONS.md"
    with path.open("a") as f:
        f.write(f"- {now_iso()} - {text.rstrip()}\n")
    index_files(store, config, [path])
    return path


def list_projects(config: Config) -> list[tuple[str, float]]:
    store = ensure_store(config)
    root = store / "projects"
    projects: list[tuple[str, float]] = []
    for path in sorted(root.iterdir() if root.exists() else []):
        if not path.is_dir():
            continue
        mtimes = [p.stat().st_mtime for p in path.rglob("*") if p.is_file()]
        projects.append((path.name, max(mtimes) if mtimes else path.stat().st_mtime))
    return sorted(projects, key=lambda item: item[1], reverse=True)

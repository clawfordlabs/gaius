from __future__ import annotations

from .config import Config
from .embeddings import embedder_status
from .indexer import index_freshness, iter_effective_external_files, vector_storage_status
from .sync import git, remote_exists


def doctor_report(config: Config) -> str:
    store = config.store
    lines = [f"Store: {store}", f"Exists: {'yes' if store.exists() else 'no'}"]
    if store.exists():
        git_ok = (store / ".git").exists()
        lines.append(f"Git: {'repo' if git_ok else 'not initialized'}")
        if git_ok:
            dirty = git(store, "status", "--porcelain", check=False).stdout.strip()
            lines.append(f"Git status: {'dirty' if dirty else 'clean'}")
            if remote_exists(store):
                remote = git(store, "remote", "-v", check=False).stdout.splitlines()[0]
                ls_remote = git(store, "ls-remote", "--heads", check=False)
                reachable = "yes" if ls_remote.returncode == 0 else "no"
                lines.append(f"Git remote: {remote}")
                lines.append(f"Git remote reachable: {reachable}")
            else:
                lines.append("Git remote: none")
        total, fresh = index_freshness(store, config)
        lines.append(f"Index: {fresh}/{total} docs fresh")
        lines.append(f"Vector index: {vector_storage_status(store)}")
    else:
        lines.append("Git: unavailable")
        lines.append("Index: unavailable")
        lines.append("Vector index: unavailable")
    if config.externals:
        lines.append("External roots:")
        for root in config.externals:
            count = len(iter_effective_external_files(store, root, config.index_max_file_mb))
            project = root.project or "none"
            exists = "yes" if root.path.exists() else "no"
            lines.append(f"  {root.path} (exists: {exists}, project: {project}, indexable: {count})")
    status = embedder_status(config)
    lines.append(f"Embedder: {status.name} ({status.detail})")
    if config.task_command:
        lines.append(f"Task command: {' '.join(config.task_command)}")
    else:
        lines.append("Task command: not configured")
    return "\n".join(lines) + "\n"

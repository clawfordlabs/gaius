from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def slugify(text: str, fallback: str = "memory") -> str:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    slug = "-".join(words[:8])
    return slug or fallback


def short_hash(text: str, length: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def title_for_project(project: str) -> str:
    return project.replace("-", " ").replace("_", " ").title()

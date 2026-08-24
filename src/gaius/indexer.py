from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
from fnmatch import fnmatchcase
from dataclasses import dataclass
from pathlib import Path

from .config import Config, ExternalRoot
from .embeddings import get_embedder
from .records import SYNC_RECORD_MARKERS
from .utils import file_hash

try:
    import sqlite_vec
except ModuleNotFoundError:
    sqlite_vec = None


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str
    offset: int


@dataclass(frozen=True)
class SearchResult:
    path: Path
    title: str
    heading: str
    snippet: str
    score: float


@dataclass(frozen=True)
class IndexableFile:
    path: Path
    index_path: Path
    project: str | None = None
    external: bool = False


@dataclass(frozen=True)
class ExternalFileScan:
    files: list[Path]
    complete: bool = True


INTERNAL_EXTENSIONS = {".md", ".markdown", ".pdf", ".txt"}
EXTERNAL_EXTENSIONS = {".md", ".markdown", ".pdf", ".txt"}


def index_path(store: Path) -> Path:
    return store / ".gaius" / "index.db"


def connect(store: Path) -> sqlite3.Connection:
    db = index_path(store)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def load_vec_extension(conn: sqlite3.Connection) -> bool:
    if sqlite_vec is None:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except sqlite3.Error:
        try:
            conn.enable_load_extension(False)
        except sqlite3.Error:
            pass
        return False


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS docs (
          id INTEGER PRIMARY KEY,
          path TEXT UNIQUE NOT NULL,
          title TEXT NOT NULL,
          mtime REAL NOT NULL,
          hash TEXT NOT NULL,
          project TEXT,
          tags TEXT,
          kind TEXT NOT NULL DEFAULT 'markdown',
          cached_text TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY,
          doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
          chunk_index INTEGER NOT NULL,
          heading TEXT NOT NULL,
          text TEXT NOT NULL,
          vector TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(text, path UNINDEXED, heading UNINDEXED, chunk_id UNINDEXED)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    conn.commit()


def ensure_vec_table(conn: sqlite3.Connection, dimensions: int) -> bool:
    if dimensions <= 0 or not load_vec_extension(conn):
        return False
    current = conn.execute("SELECT value FROM vector_meta WHERE key = 'dimensions'").fetchone()
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'vec_chunks'").fetchone()
    if exists and current and int(current["value"]) == dimensions:
        return True
    dimensions_changed = current is not None and int(current["value"]) != dimensions
    conn.execute("DROP TABLE IF EXISTS vec_chunks")
    if dimensions_changed:
        conn.execute("UPDATE chunks SET vector = NULL")
    conn.execute(f"CREATE VIRTUAL TABLE vec_chunks USING vec0(chunk_id integer primary key, embedding float[{dimensions}])")
    conn.execute(
        "INSERT OR REPLACE INTO vector_meta(key, value) VALUES ('dimensions', ?)",
        (str(dimensions),),
    )
    conn.commit()
    return True


def vector_config_key(config: Config) -> str:
    if config.embedder == "fastembed":
        return f"fastembed:{config.fastembed_model or ''}"
    if config.embedder == "openai":
        return f"openai:{config.openai_base_url or ''}:{config.openai_model or ''}"
    if config.embedder == "auto":
        return f"auto:{config.fastembed_model or ''}"
    return config.embedder


def ensure_vector_config(conn: sqlite3.Connection, config: Config) -> bool:
    key = vector_config_key(config)
    current = conn.execute("SELECT value FROM vector_meta WHERE key = 'embedder'").fetchone()
    if current and current["value"] == key:
        return False
    load_vec_extension(conn)
    conn.execute("DROP TABLE IF EXISTS vec_chunks")
    conn.execute("DELETE FROM vector_meta")
    conn.execute("UPDATE chunks SET vector = NULL")
    conn.execute("INSERT INTO vector_meta(key, value) VALUES ('embedder', ?)", (key,))
    conn.commit()
    return True


def vector_config_current(conn: sqlite3.Connection) -> str | None:
    current = conn.execute("SELECT value FROM vector_meta WHERE key = 'embedder'").fetchone()
    return str(current["value"]) if current else None


def vector_freshness_required(config: Config) -> bool:
    try:
        return get_embedder(config).name != "none"
    except Exception:
        return False


def doc_vectors_complete(conn: sqlite3.Connection, doc_id: int) -> bool:
    missing = conn.execute(
        "SELECT COUNT(*) AS count FROM chunks WHERE doc_id = ? AND vector IS NULL",
        (doc_id,),
    ).fetchone()
    return int(missing["count"]) == 0


def vec_table_active(conn: sqlite3.Connection) -> bool:
    if not load_vec_extension(conn):
        return False
    return conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'vec_chunks'").fetchone() is not None


def vector_storage_status(store: Path) -> str:
    conn = connect(store)
    ensure_schema(conn)
    if vec_table_active(conn):
        dimensions = conn.execute("SELECT value FROM vector_meta WHERE key = 'dimensions'").fetchone()
        suffix = f" ({dimensions['value']} dimensions)" if dimensions else ""
        conn.close()
        return f"sqlite-vec{suffix}"
    if load_vec_extension(conn):
        conn.close()
        return "sqlite-vec available; no vector table yet"
    conn.close()
    return "json fallback"


def chunk_markdown(relative_path: str, text: str, max_tokens: int = 400) -> list[Chunk]:
    sections: list[tuple[str, list[str], int]] = []
    stack: list[tuple[int, str]] = []
    current_heading = Path(relative_path).stem
    current_lines: list[str] = []
    current_offset = 0

    for line_no, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if stripped in SYNC_RECORD_MARKERS:
            continue
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= hashes <= 6 and stripped[hashes : hashes + 1] == " ":
                if current_lines:
                    sections.append((current_heading, current_lines, current_offset))
                title = stripped[hashes:].strip()
                stack = [(lvl, name) for lvl, name in stack if lvl < hashes]
                stack.append((hashes, title))
                current_heading = " > ".join(name for _, name in stack)
                current_lines = []
                current_offset = line_no
                continue
        current_lines.append(line)

    if current_lines or not sections:
        sections.append((current_heading, current_lines, current_offset))

    chunks: list[Chunk] = []
    for heading, lines, offset in sections:
        body_words = "\n".join(lines).strip().split()
        if not body_words:
            body_words = []
        for i in range(0, max(1, len(body_words)), max_tokens):
            words = body_words[i : i + max_tokens]
            body = " ".join(words) if words else ""
            prefix = f"Path: {relative_path}\nHeading: {heading}\n\n"
            chunks.append(Chunk(heading=heading, text=prefix + body, offset=offset + i))
    return chunks


def extract_title(text: str, path: Path) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or path.stem
    return path.stem


def infer_project(relative: Path) -> str | None:
    parts = relative.parts
    if len(parts) >= 2 and parts[0] == "projects":
        return parts[1]
    return None


def parse_tags(text: str) -> list[str]:
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    tags: list[str] = []
    for line in text[3:end].splitlines():
        if line.startswith("tags:"):
            raw = line.split(":", 1)[1].strip().strip("[]")
            tags = [part.strip() for part in raw.split(",") if part.strip()]
    return tags


def sanitize_fts_query(query: str) -> str:
    """Tokenize unsafe user text and OR quoted FTS5 phrases for broad recall."""
    terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _matches_globs(relative: Path, patterns: tuple[str, ...]) -> bool:
    rel = relative.as_posix()
    return any(fnmatchcase(rel, pattern) for pattern in patterns)


def _external_root_contains(root: ExternalRoot, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.path.resolve())
        return True
    except ValueError:
        return False


def external_project_for_path(config: Config, path: Path) -> str | None:
    containing = [root for root in config.externals if _external_root_contains(root, path)]
    if not containing:
        return None
    root = max(containing, key=lambda item: len(item.path.resolve().parts))
    return root.project


def _root_relative_path(root: ExternalRoot, path: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.path.resolve())
    except ValueError:
        return None


def _external_file_allowed(root: ExternalRoot, path: Path, max_bytes: int) -> bool:
    relative = _root_relative_path(root, path)
    if relative is None:
        return False
    if ".git" in relative.parts:
        return False
    if path.suffix.lower() not in EXTERNAL_EXTENSIONS:
        return False
    if root.include and not _matches_globs(relative, root.include):
        return False
    if root.exclude and _matches_globs(relative, root.exclude):
        return False
    try:
        if path.stat().st_size > max_bytes:
            return False
    except OSError:
        return False
    return True


def _git_ls_files(root: Path) -> tuple[list[Path], bool] | None:
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return [], False
    if result.returncode != 0:
        return [], False
    return [root / raw.decode() for raw in result.stdout.split(b"\0") if raw], True


def scan_external_files(root: ExternalRoot, max_file_mb: float = 10.0) -> ExternalFileScan:
    if not root.path.exists() or not root.path.is_dir():
        return ExternalFileScan([], complete=False)
    max_bytes = int(max_file_mb * 1024 * 1024)
    git_scan = _git_ls_files(root.path)
    if git_scan is None:
        candidates = list(root.path.rglob("*"))
        complete = True
    else:
        candidates, complete = git_scan
    files = [
        path
        for path in candidates
        if path.is_file() and _external_file_allowed(root, path, max_bytes)
    ]
    return ExternalFileScan(sorted(set(files), key=lambda path: str(path.resolve())), complete=complete)


def iter_external_files(root: ExternalRoot, max_file_mb: float = 10.0) -> list[Path]:
    return scan_external_files(root, max_file_mb).files


def is_inside_store(store: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(store.resolve())
        return True
    except ValueError:
        return False


def iter_effective_external_files(store: Path, root: ExternalRoot, max_file_mb: float = 10.0) -> list[Path]:
    return [path for path in iter_external_files(root, max_file_mb) if not is_inside_store(store, path)]


def iter_indexable_files_with_failures(store: Path, config: Config) -> tuple[list[IndexableFile], tuple[Path, ...]]:
    files: list[IndexableFile] = []
    incomplete_external_roots: list[Path] = []
    roots = [store / "global", store / "projects"]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in INTERNAL_EXTENSIONS:
                identity = relative_to_store(store, path)
                files.append(IndexableFile(path=path, index_path=identity, project=infer_project(identity)))
    for root in config.externals:
        scan = scan_external_files(root, config.index_max_file_mb)
        if not scan.complete:
            incomplete_external_roots.append(root.path.resolve())
        for path in [path for path in scan.files if not is_inside_store(store, path)]:
            resolved = path.resolve()
            files.append(IndexableFile(path=path, index_path=resolved, project=external_project_for_path(config, path), external=True))
    unique = {str(file.index_path): file for file in files}
    return sorted(unique.values(), key=lambda file: str(file.index_path)), tuple(incomplete_external_roots)


def iter_indexable_files(store: Path, config: Config) -> list[IndexableFile]:
    files, _ = iter_indexable_files_with_failures(store, config)
    return files


def read_indexable_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(errors="replace")


def relative_to_store(store: Path, path: Path) -> Path:
    try:
        return path.resolve().relative_to(store.resolve())
    except ValueError:
        return path.resolve()


def _as_indexable_file(store: Path, config: Config, file: Path | IndexableFile) -> IndexableFile:
    if isinstance(file, IndexableFile):
        return file
    identity = relative_to_store(store, file)
    if identity.is_absolute():
        return IndexableFile(path=file, index_path=identity, project=external_project_for_path(config, file), external=True)
    return IndexableFile(path=file, index_path=identity, project=infer_project(identity))


def index_files(
    store: Path,
    config: Config,
    files: list[Path | IndexableFile],
    rebuild: bool = False,
    cleanup_stale: bool = False,
    incomplete_external_roots: tuple[Path, ...] = (),
) -> int:
    conn = connect(store)
    ensure_schema(conn)
    if rebuild:
        load_vec_extension(conn)
        conn.execute("DROP TABLE IF EXISTS vec_chunks")
        conn.execute("DELETE FROM vector_meta")
        conn.execute("DELETE FROM chunks_fts")
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM docs")
        conn.commit()

    embedder = get_embedder(config)
    vector_config_changed = ensure_vector_config(conn, config) if embedder.name != "none" else False
    indexed = 0
    live_paths = {str(_as_indexable_file(store, config, file).index_path) for file in files}
    if cleanup_stale:
        existing_paths = [row["path"] for row in conn.execute("SELECT path FROM docs").fetchall()]
        stale_paths = [
            path
            for path in existing_paths
            if (
                path.startswith("vaults/")
                or (
                    Path(path).is_absolute()
                    and path not in live_paths
                    and not any(is_inside_store(root, Path(path)) for root in incomplete_external_roots)
                )
            )
        ]
        if stale_paths:
            with conn:
                for stale_path in stale_paths:
                    chunk_ids = [
                        int(row["id"])
                        for row in conn.execute(
                            """
                            SELECT chunks.id
                            FROM chunks
                            JOIN docs ON docs.id = chunks.doc_id
                            WHERE docs.path = ?
                            """,
                            (stale_path,),
                        ).fetchall()
                    ]
                    if chunk_ids:
                        placeholders = ",".join("?" for _ in chunk_ids)
                        conn.execute(f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})", chunk_ids)
                        if vec_table_active(conn):
                            conn.execute(f"DELETE FROM vec_chunks WHERE chunk_id IN ({placeholders})", chunk_ids)
                    conn.execute("DELETE FROM docs WHERE path = ?", (stale_path,))
    for file in files:
        candidate = _as_indexable_file(store, config, file)
        path = candidate.path
        if not path.exists() or not path.is_file():
            continue
        rel = candidate.index_path
        digest = file_hash(path)
        mtime = path.stat().st_mtime
        existing = conn.execute("SELECT id, mtime, hash, project FROM docs WHERE path = ?", (str(rel),)).fetchone()
        vectors_complete = True
        if existing and embedder.name != "none":
            vectors_complete = doc_vectors_complete(conn, int(existing["id"]))
        if (
            existing
            and existing["hash"] == digest
            and abs(existing["mtime"] - mtime) < 0.0001
            and existing["project"] == candidate.project
            and not vector_config_changed
            and vectors_complete
        ):
            continue

        text = read_indexable_text(path)
        title = extract_title(text, path)
        chunks = chunk_markdown(str(rel), text)
        vectors = embedder.embed([chunk.text for chunk in chunks]) if embedder.name != "none" else []
        use_vec = ensure_vec_table(conn, len(vectors[0])) if vectors else False

        with conn:
            if existing:
                doc_id = int(existing["id"])
                old_chunk_ids = [
                    int(row["id"]) for row in conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,)).fetchall()
                ]
                if old_chunk_ids and vec_table_active(conn):
                    placeholders = ",".join("?" for _ in old_chunk_ids)
                    conn.execute(f"DELETE FROM vec_chunks WHERE chunk_id IN ({placeholders})", old_chunk_ids)
                conn.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id = ?)", (doc_id,))
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                conn.execute(
                    """
                    UPDATE docs
                    SET title = ?, mtime = ?, hash = ?, project = ?, tags = ?, kind = ?, cached_text = ?
                    WHERE id = ?
                    """,
                    (
                        title,
                        mtime,
                        digest,
                        candidate.project,
                        json.dumps(parse_tags(text)),
                        "pdf" if path.suffix.lower() == ".pdf" else "markdown",
                        text if path.suffix.lower() == ".pdf" else None,
                        doc_id,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO docs(path, title, mtime, hash, project, tags, kind, cached_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(rel),
                        title,
                        mtime,
                        digest,
                        candidate.project,
                        json.dumps(parse_tags(text)),
                        "pdf" if path.suffix.lower() == ".pdf" else "markdown",
                        text if path.suffix.lower() == ".pdf" else None,
                    ),
                )
                doc_id = int(cur.lastrowid)

            for idx, chunk in enumerate(chunks):
                vector = json.dumps(vectors[idx]) if vectors else None
                cur = conn.execute(
                    "INSERT INTO chunks(doc_id, chunk_index, heading, text, vector) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, idx, chunk.heading, chunk.text, vector),
                )
                chunk_id = int(cur.lastrowid)
                conn.execute(
                    "INSERT INTO chunks_fts(rowid, text, path, heading, chunk_id) VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, chunk.text, str(rel), chunk.heading, chunk_id),
                )
                if use_vec:
                    conn.execute(
                        "INSERT INTO vec_chunks(chunk_id, embedding) VALUES (?, ?)",
                        (chunk_id, sqlite_vec.serialize_float32(vectors[idx])),
                    )
        indexed += 1
    conn.close()
    return indexed


def index_store(store: Path, config: Config, rebuild: bool = False) -> int:
    files, incomplete_external_roots = iter_indexable_files_with_failures(store, config)
    return index_files(
        store,
        config,
        files,
        rebuild=rebuild,
        cleanup_stale=True,
        incomplete_external_roots=incomplete_external_roots,
    )


def vector_score(query_vector: list[float], vector_json: str | None) -> float:
    if not query_vector or not vector_json:
        return 0.0
    vector = json.loads(vector_json)
    dot = sum(a * b for a, b in zip(query_vector, vector))
    qn = math.sqrt(sum(a * a for a in query_vector))
    vn = math.sqrt(sum(b * b for b in vector))
    if qn == 0 or vn == 0:
        return 0.0
    return dot / (qn * vn)


def search(store: Path, config: Config, query: str, project: str | None = None, limit: int = 10) -> list[SearchResult]:
    index_store(store, config, rebuild=False)
    fts_query = sanitize_fts_query(query)
    if not fts_query:
        return []
    conn = connect(store)
    ensure_schema(conn)
    params: list[object] = [fts_query]
    project_clause = ""
    if project:
        project_clause = "AND docs.project = ?"
        params.append(project)

    rows = conn.execute(
        f"""
        SELECT chunks.id AS chunk_id, docs.path, docs.title, chunks.heading, chunks.text,
               bm25(chunks_fts) AS bm25, chunks.vector
        FROM chunks_fts
        JOIN chunks ON chunks.id = chunks_fts.chunk_id
        JOIN docs ON docs.id = chunks.doc_id
        WHERE chunks_fts MATCH ? {project_clause}
        ORDER BY bm25(chunks_fts)
        LIMIT ?
        """,
        [*params, max(limit * 4, limit)],
    ).fetchall()

    rank_scores: dict[int, float] = {}
    row_by_id: dict[int, sqlite3.Row] = {}
    for rank, row in enumerate(rows, start=1):
        row_by_id[int(row["chunk_id"])] = row
        rank_scores[int(row["chunk_id"])] = rank_scores.get(int(row["chunk_id"]), 0.0) + 1.0 / (60 + rank)

    embedder = get_embedder(config)
    if embedder.name != "none":
        qvec = embedder.embed([query])[0]
        ensure_vec_table(conn, len(qvec))
        scored = []
        if vec_table_active(conn):
            vec_params: list[object] = [sqlite_vec.serialize_float32(qvec), max(limit * 4, limit)]
            if project:
                vec_params.append(project)
            scored = conn.execute(
                f"""
                SELECT chunks.id AS chunk_id, docs.path, docs.title, chunks.heading, chunks.text,
                       chunks.vector, vec_chunks.distance
                FROM vec_chunks
                JOIN chunks ON chunks.id = vec_chunks.chunk_id
                JOIN docs ON docs.id = chunks.doc_id
                WHERE vec_chunks.embedding MATCH ? AND k = ? {project_clause}
                ORDER BY vec_chunks.distance
                """,
                vec_params,
            ).fetchall()
        if not scored:
            vec_rows = conn.execute(
                f"""
                SELECT chunks.id AS chunk_id, docs.path, docs.title, chunks.heading, chunks.text, chunks.vector
                FROM chunks
                JOIN docs ON docs.id = chunks.doc_id
                WHERE chunks.vector IS NOT NULL {project_clause}
                """,
                [project] if project else [],
            ).fetchall()
            scored = sorted(vec_rows, key=lambda r: vector_score(qvec, r["vector"]), reverse=True)[: max(limit * 4, limit)]
        for rank, row in enumerate(scored, start=1):
            row_by_id[int(row["chunk_id"])] = row
            rank_scores[int(row["chunk_id"])] = rank_scores.get(int(row["chunk_id"]), 0.0) + 1.0 / (60 + rank)

    ordered = sorted(rank_scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    results = [
        SearchResult(
            path=Path(row_by_id[chunk_id]["path"]),
            title=row_by_id[chunk_id]["title"],
            heading=row_by_id[chunk_id]["heading"],
            snippet=" ".join(row_by_id[chunk_id]["text"].split())[:500],
            score=score,
        )
        for chunk_id, score in ordered
    ]
    conn.close()
    return results


def index_freshness(store: Path, config: Config) -> tuple[int, int]:
    files = iter_indexable_files(store, config)
    if not index_path(store).exists():
        return len(files), 0
    conn = connect(store)
    ensure_schema(conn)
    current_vector_config = vector_config_current(conn)
    expected_vector_config = vector_config_key(config)
    vector_config_matches = current_vector_config is None or current_vector_config == expected_vector_config
    vectors_required = vector_freshness_required(config)
    indexed_rows = conn.execute("SELECT id, path, mtime, hash, project FROM docs").fetchall()
    indexed = {row["path"]: row for row in indexed_rows}
    fresh = 0
    for file in files:
        rel = str(file.index_path)
        row = indexed.get(rel)
        if not row or row["hash"] != file_hash(file.path) or row["project"] != file.project:
            continue
        if vectors_required and current_vector_config is not None:
            if not vector_config_matches or not doc_vectors_complete(conn, int(row["id"])):
                continue
        if row:
            fresh += 1
    conn.close()
    return len(files), fresh

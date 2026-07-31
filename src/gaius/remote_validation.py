from __future__ import annotations

from pathlib import Path


MAX_SEGMENT_CHARS = 128
MAX_DOCUMENT_BYTES = 1024 * 1024
GLOB_CHARS = frozenset("*?[]")
REMOTE_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
PRIVATE_STORE_PARTS = frozenset({".git", ".gaius"})


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


def validate_path_segment(name: str, value: str) -> str:
    validate_segment(name, value)
    if value in PRIVATE_STORE_PARTS:
        raise RemoteValidationError(f"{name} must not name private store metadata")
    return value


def resolve_store_path(store: Path, path: Path) -> Path:
    if path.is_absolute():
        raise RemoteValidationError("path must be relative to the memory store")
    root = store.resolve()
    resolved = (root / path).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise RemoteValidationError("path escapes the memory store") from exc
    if any(part in PRIVATE_STORE_PARTS for part in relative.parts):
        raise RemoteValidationError("path points at private store metadata")
    return resolved


def validate_document_reference(value: str) -> Path:
    if not value or Path(value).is_absolute() or "\0" in value:
        raise RemoteValidationError("document must be a store-relative path or ID")
    if any(character in value for character in GLOB_CHARS):
        raise RemoteValidationError("document must not contain glob characters")
    candidate = Path(value)
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise RemoteValidationError("document contains an unsafe path component")
    if any(part in PRIVATE_STORE_PARTS for part in candidate.parts):
        raise RemoteValidationError("document points at private store metadata")
    return candidate


def store_relative_path(store: Path, path: Path) -> Path | None:
    if path.is_absolute():
        return None
    try:
        resolved = resolve_store_path(store, path)
    except RemoteValidationError:
        return None
    return resolved.relative_to(store.resolve())


def resolve_store_document(store: Path, value: str) -> Path:
    reference = validate_document_reference(value)
    root = store.resolve()
    candidates = [root / reference]
    if len(reference.parts) == 1:
        candidates.extend(root.rglob(reference.name))
        if not reference.suffix:
            candidates.extend(root.rglob(f"{reference.name}.md"))

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            unresolved_relative = candidate.relative_to(root)
        except ValueError:
            continue
        relative = store_relative_path(root, unresolved_relative)
        if relative is None or relative in seen:
            continue
        seen.add(relative)
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

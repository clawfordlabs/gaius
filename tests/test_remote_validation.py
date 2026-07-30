from pathlib import Path

import pytest

from gaius.remote_validation import (
    MAX_DOCUMENT_BYTES,
    RemoteValidationError,
    resolve_store_document,
    store_relative_path,
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


@pytest.mark.parametrize(
    "value",
    [
        "/etc/passwd",
        "../outside.md",
        "notes/*.md",
        "notes/[ab].md",
        ".git/hooks/readme.txt",
        ".gaius/debug.txt",
    ],
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

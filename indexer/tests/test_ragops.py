"""Catch document-level update regressions with an in-memory collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pipeline import ragops
from pipeline.config import IndexerSettings


class Collection:
    name = "assignment-three"

    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata
        self.rows: dict[str, dict[str, Any]] = {}

    def get(self, include: list[str]) -> dict[str, Any]:
        return {"ids": list(self.rows), "metadatas": list(self.rows.values())}

    def count(self) -> int:
        return len(self.rows)

    def modify(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata

    def upsert(self, ids: list[str], documents: list[str], embeddings: list[list[float]],
               metadatas: list[dict[str, Any]]) -> None:
        self.rows.update(zip(ids, metadatas, strict=True))

    def delete(self, ids: list[str]) -> None:
        for key in ids:
            del self.rows[key]

    def update(self, ids: list[str], metadatas: list[dict[str, Any]]) -> None:
        for key, metadata in zip(ids, metadatas, strict=True):
            self.rows[key].update(metadata)


class Client:
    collection: Collection | None = None

    def list_collections(self) -> list[Collection]:
        return [self.collection] if self.collection else []

    def get_collection(self, name: str) -> Collection | None:
        return self.collection

    def create_collection(self, name: str, metadata: dict[str, Any],
                          configuration: dict[str, Any]) -> Collection:
        self.collection = Collection(metadata)
        return self.collection


def test_changes_delete_and_restore_selectively(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    lake = tmp_path / "lake"
    output = tmp_path / "out"
    store = Client()
    settings = IndexerSettings("encoder", "chroma", 8000, "assignment-three", str(inbox), 400, 50, "")
    monkeypatch.setattr(ragops, "model_version", lambda _: "digest")
    monkeypatch.setattr(ragops, "get_client", lambda _: store)
    monkeypatch.setattr(ragops, "embed", lambda texts, _: [
        [0.0, 1.0] if "workshop" in text else [1.0, 0.0] for text in texts
    ])
    current = {"file": "rules.md", "document_id": "rules", "doc_version": "2026", "source": "test",
               "title": "University Rules", "page_count": 1}
    other = {**current, "file": "workshop.md", "document_id": "workshop"}
    newer = {**current, "file": "replacement.md", "doc_version": "2027"}
    (inbox / current["file"]).write_text("# Academic rules\n\nThese rules describe academic status.")
    (inbox / other["file"]).write_text("# Technical workshop\n\nThe workshop provides a practice kit.")
    (inbox / newer["file"]).write_text("# Academic rules\n\nThese rules now use a new grading scheme.")

    def run(rows: list[dict[str, Any]], restore: str | None = None) -> dict[str, Any]:
        before = set(output.glob("*.json"))
        ragops.save(inbox / "_ragops.json", rows)
        ragops.ingest(settings, lake, output, .97, restore, None)
        new = set(output.glob("*.json")) - before
        return json.loads(new.pop().read_text())["index_update"]

    assert run([current, other])["chunks_embedded"] == 2
    assert run([current, other])["chunks_embedded"] == 0
    update = run([current, newer])
    assert update["chunks_embedded"] == 1
    assert update["counts_by_document_id"]["workshop"] == {"active": 0, "archived": 0}
    assert (lake / "archive/rules/2026/rules.md").is_file()
    assert store.collection is not None
    active = [item["doc_version"] for item in store.collection.rows.values() if not item["superseded"]]
    assert active == ["2027"]
    (inbox / current["file"]).unlink()
    # Restore from verified history after the old raw file leaves the inbox.
    restored = run([newer], "rules=2026")
    assert restored["chunks_embedded"] == 0
    active = [item["doc_version"] for item in store.collection.rows.values() if not item["superseded"]]
    assert active == ["2026"]

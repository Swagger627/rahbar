"""Storage tests against a small in-memory fake of the Chroma client: metadata, repeated
indexing, a model change, and failures that must leave the old index alone."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from pipeline.config import get_settings
from pipeline.models import Chunk
from pipeline.store import write_index

SETTINGS = replace(get_settings(), embed_model="nomic-embed-text", collection="rahbar")


class FakeCollection:
    def __init__(self, client: FakeClient, name: str, metadata: dict[str, Any]) -> None:
        self.client, self.name, self.metadata = client, name, metadata
        self.rows: dict[str, tuple[str, list[float], dict[str, Any]]] = {}
        self.lose_rows = False          # pretend some writes went missing

    def add(self, ids: list[str], documents: list[str], embeddings: list[list[float]],
            metadatas: list[dict[str, Any]]) -> None:
        for i, d, e, m in zip(ids, documents, embeddings, metadatas):
            if not self.lose_rows:
                self.rows[i] = (d, e, m)

    def count(self) -> int:
        return len(self.rows)

    def modify(self, name: str) -> None:
        self.client.collections[name] = self.client.collections.pop(self.name)
        self.name = name


class FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}
        self.lose_rows = False

    def list_collections(self) -> list[FakeCollection]:
        return list(self.collections.values())

    def get_collection(self, name: str) -> FakeCollection:
        return self.collections[name]

    def create_collection(self, name: str, metadata: dict[str, Any]) -> FakeCollection:
        assert name not in self.collections, "create_collection on an existing name"
        self.collections[name] = FakeCollection(self, name, metadata)
        self.collections[name].lose_rows = self.lose_rows
        return self.collections[name]

    def delete_collection(self, name: str) -> None:
        del self.collections[name]


def make_chunks(n: int, source: str = "rules.pdf") -> list[Chunk]:
    return [Chunk(id=f"rules-{i:04d}", text=f"Grade Points\ntext {i}", source=source,
                  section="Grade Points", page=i + 1, doc_version="2026", superseded=False)
            for i in range(n)]


def vectors(n: int, dim: int = 4) -> list[list[float]]:
    return [[float(i)] * dim for i in range(n)]


def test_chunks_vectors_and_metadata_are_stored() -> None:
    client = FakeClient()
    assert write_index(client, make_chunks(3), vectors(3), SETTINGS) == 3
    assert set(client.collections) == {"rahbar"}            # staging was renamed away
    col = client.collections["rahbar"]
    assert col.metadata == {"hnsw:space": "cosine", "embed_model": "nomic-embed-text",
                            "dimension": 4}
    text, vector, meta = col.rows["rules-0001"]
    assert text == "Grade Points\ntext 1" and vector == [1.0] * 4
    assert meta == {"source": "rules.pdf", "section": "Grade Points", "page": 2,
                    "doc_version": "2026", "superseded": False}


def test_more_chunks_than_one_add_batch() -> None:
    client = FakeClient()
    assert write_index(client, make_chunks(250), vectors(250), SETTINGS) == 250


def test_indexing_twice_replaces_rather_than_duplicates() -> None:
    client = FakeClient()
    write_index(client, make_chunks(5), vectors(5), SETTINGS)
    assert write_index(client, make_chunks(3), vectors(3), SETTINGS) == 3
    assert set(client.collections) == {"rahbar"}
    assert client.collections["rahbar"].count() == 3          # no rows left from the first run


def test_a_new_model_never_mixes_with_the_old_index() -> None:
    client = FakeClient()
    write_index(client, make_chunks(3), vectors(3, dim=768), replace(SETTINGS, embed_model="old"))
    write_index(client, make_chunks(2), vectors(2, dim=4), SETTINGS)
    col = client.collections["rahbar"]
    assert col.metadata["embed_model"] == "nomic-embed-text" and col.metadata["dimension"] == 4
    assert {len(v) for _, v, _ in col.rows.values()} == {4}


def test_leftover_staging_from_a_crashed_run_is_replaced() -> None:
    client = FakeClient()
    client.create_collection("rahbar-staging", {"embed_model": "half-finished"})
    write_index(client, make_chunks(2), vectors(2), SETTINGS)
    assert set(client.collections) == {"rahbar"}


def test_failed_write_keeps_the_old_index() -> None:
    client = FakeClient()
    write_index(client, make_chunks(3), vectors(3), SETTINGS)
    client.lose_rows = True
    with pytest.raises(RuntimeError, match="staging holds 0 rows, expected 2"):
        write_index(client, make_chunks(2), vectors(2), SETTINGS)
    assert set(client.collections) == {"rahbar"}
    assert client.collections["rahbar"].count() == 3          # the working index survived


@pytest.mark.parametrize("chunks, vecs, message", [
    ([], [], "no chunks"),
    (make_chunks(2), vectors(1), "2 chunks but 1 vectors"),
    (make_chunks(1) * 2, vectors(2), "not unique"),
    (make_chunks(2), [[0.1, 0.2], [0.1, 0.2, 0.3]], "different dimensions"),
])
def test_bad_input_is_refused_before_chroma_is_touched(chunks: list[Chunk],
                                                       vecs: list[list[float]],
                                                       message: str) -> None:
    client = FakeClient()
    write_index(client, make_chunks(3), vectors(3), SETTINGS)
    with pytest.raises(RuntimeError, match=message):
        write_index(client, chunks, vecs, SETTINGS)
    assert set(client.collections) == {"rahbar"} and client.collections["rahbar"].count() == 3

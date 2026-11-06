"""Stage 5: Store. The only code in the whole system that writes to Chroma."""

from __future__ import annotations

from typing import Any

from .config import IndexerSettings
from .models import Chunk
from .run import log

ADD_BATCH = 100   # rows per add() call, well under Chroma's per-request limit


def get_client(settings: IndexerSettings) -> Any:
    import chromadb  # the thin client, from chromadb-client in requirements.txt
    from chromadb.config import Settings as ChromaSettings

    return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port,
                               settings=ChromaSettings(anonymized_telemetry=False))


def store(chunks: list[Chunk], vectors: list[list[float]], settings: IndexerSettings) -> int:
    """Write every chunk with its vector and metadata, and record the embedding model
    and vector dimension in the collection metadata (section 4.4). Return the count."""
    return write_index(get_client(settings), chunks, vectors, settings)


def write_index(client: Any, chunks: list[Chunk], vectors: list[list[float]],
                settings: IndexerSettings) -> int:
    """Build the new index beside the old one, check it, then swap it in.

    1. Write everything into "<collection>-staging", a brand-new collection, so vectors
       from two models can never be mixed in one collection.
    2. Check that staging holds exactly one row per chunk.
    3. Only then delete the old collection and rename staging to the real name.

    If anything fails before step 3, the old index is untouched and the API keeps
    serving it. Limitation: between the delete and the rename (milliseconds) the
    collection does not exist, so a question asked in that instant fails once.
    """
    _check_inputs(chunks, vectors)
    dimension = len(vectors[0])
    staging_name = f"{settings.collection}-staging"
    _delete_if_present(client, staging_name)   # left behind by a run that crashed
    staging = client.create_collection(
        name=staging_name,
        metadata={"hnsw:space": "cosine", "embed_model": settings.embed_model,
                  "dimension": dimension},
    )
    for start in range(0, len(chunks), ADD_BATCH):
        batch = chunks[start:start + ADD_BATCH]
        staging.add(ids=[c.id for c in batch],
                    documents=[c.text for c in batch],
                    embeddings=vectors[start:start + ADD_BATCH],
                    metadatas=[c.metadata() for c in batch])
    count = int(staging.count())
    if count != len(chunks):
        _delete_if_present(client, staging_name)
        raise RuntimeError(f"staging holds {count} rows, expected {len(chunks)}; "
                           f"the old index {settings.collection!r} was left unchanged")
    old = _old_model(client, settings.collection)
    if old and old != settings.embed_model:
        log("replacing an index built with a different model", old_model=old,
            new_model=settings.embed_model)
    _delete_if_present(client, settings.collection)
    staging.modify(name=settings.collection)
    return count


def _check_inputs(chunks: list[Chunk], vectors: list[list[float]]) -> None:
    if not chunks:
        raise RuntimeError("no chunks to store; refusing to replace the index with an empty one")
    if len(chunks) != len(vectors):
        raise RuntimeError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    ids = [c.id for c in chunks]
    if len(set(ids)) != len(ids):
        raise RuntimeError("chunk ids are not unique")
    if len({len(v) for v in vectors}) != 1:
        raise RuntimeError("vectors have different dimensions; they cannot share one collection")


def _delete_if_present(client: Any, name: str) -> None:
    if name in _collection_names(client):
        client.delete_collection(name)


def _old_model(client: Any, name: str) -> str | None:
    if name not in _collection_names(client):
        return None
    metadata = client.get_collection(name).metadata or {}
    return metadata.get("embed_model")


def _collection_names(client: Any) -> list[str]:
    return [c.name for c in client.list_collections()]   # chromadb 1.0 returns Collection objects

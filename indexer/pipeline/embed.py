"""Stage 4: Embed. One vector per chunk, from the pinned model only."""

from __future__ import annotations

import math

import httpx

from .config import IndexerSettings
from .dummy import DUMMY_MODEL

BATCH_SIZE = 16          # chunks per request: 398 chunks take 25 requests
TIMEOUT_SECONDS = 120    # the first request also loads the model into memory, so allow time
CONNECT_SECONDS = 10     # but fail fast when Ollama is not running at all


def embed(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Embed with settings.embed_model and nothing else.

    With Ollama: POST {ollama_url}/api/embed with {"model": ..., "input": [texts]}
    returns {"embeddings": [[...], ...]}. Batch the calls; one request per chunk is slow.

    Any failure stops the run with a clear error. There is no fallback to fake vectors:
    a half-real index would answer questions with nonsense and no error anywhere.
    """
    if settings.embed_model == DUMMY_MODEL:
        raise RuntimeError(f"EMBED_MODEL is {DUMMY_MODEL!r}, which only the --dummy mode uses; "
                           "set it to a real Ollama model such as nomic-embed-text")
    url = settings.ollama_url.rstrip("/") + "/api/embed"
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        where = f"chunks {start}-{start + len(batch) - 1} with {settings.embed_model!r} at {url}"
        try:
            # truncate=False: a chunk longer than the model's context is an error, not
            # silently cut short.
            response = httpx.post(url, json={"model": settings.embed_model, "input": batch,
                                             "truncate": False},
                                  timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_SECONDS))
        except httpx.HTTPError as exc:
            raise RuntimeError(f"embedding {where} failed: {exc!r}") from exc
        if response.status_code != 200:
            # Ollama explains itself in the body, e.g. 'model "x" not found, try pulling it first'
            raise RuntimeError(f"embedding {where} failed: HTTP {response.status_code} "
                               f"{response.text[:300]}")
        try:
            got = response.json()["embeddings"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"embedding {where}: response has no 'embeddings' list") from exc
        if not isinstance(got, list) or len(got) != len(batch):
            count = len(got) if isinstance(got, list) else "no"
            raise RuntimeError(f"embedding {where}: sent {len(batch)} texts, got {count} vectors")
        vectors.extend(got)
    _check_vectors(vectors)
    return vectors


def _check_vectors(vectors: list[list[float]]) -> None:
    """Every vector must be a non-empty list of finite numbers, all the same length.
    Vectors of two lengths in one index mean two models were mixed."""
    if not vectors:
        return
    dimension = len(vectors[0]) if isinstance(vectors[0], list) else 0
    for i, vector in enumerate(vectors):
        if not isinstance(vector, list) or not vector:
            raise RuntimeError(f"embedding {i} is empty or not a list")
        if len(vector) != dimension:
            raise RuntimeError(f"embedding {i} has dimension {len(vector)}, "
                               f"but embedding 0 has {dimension}")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in vector):
            raise RuntimeError(f"embedding {i} contains a value that is not a finite number")

"""The real retriever (sections 3.2 and 4.4).

A question is embedded with the same Ollama model that indexed the chunks, and Chroma
returns the stored chunks whose vectors are closest to it. Nothing here writes to
Chroma: the indexer Job is the only writer.
"""

from __future__ import annotations

import math
import re
from typing import Any

import httpx

from ..config import Settings
from ..schemas import Passage

EMBED_TIMEOUT_SECONDS = 30   # one short question; the model may need loading first
CONNECT_SECONDS = 5

# Words that mean the user asks about an older policy, or how a policy changed.
# "previous" is left out on purpose: "ignore all previous instructions" is an injection,
# not a history question.
HISTORY_WORDS = re.compile(
    r"\b(old|older|earlier|former|superseded|outdated|used to|"
    r"changed?|compare|comparison|difference|differ)\b", re.IGNORECASE)
# A plain year such as "2023", but not an academic-year range such as "2026-27".
YEAR = re.compile(r"\b(20\d\d)\b(?!\s*-\s*\d)")
# An explicit clause reference in a question: "clause 1.21", "section 5.4", "rule 3.2.1".
# Embeddings blur exact numbers, so these are also looked up as exact text. A bare number
# ("a CGPA below 2.00") is not a clause reference.
CLAUSE_REF = re.compile(r"\b(?:clause|section|rule|article|item)\s+(\d+(?:\.\d+)+)\b",
                        re.IGNORECASE)
# Only the closest chunk that contains the clause: clause numbers repeat across documents
# ("5.4" is also a clause of the entrepreneurship policy), so a second match is mostly noise.
EXACT_PER_CLAUSE = 1


def clause_refs(question: str) -> list[str]:
    """The clause numbers a question names explicitly, e.g. ["1.21"]."""
    return CLAUSE_REF.findall(question)


def mentions_clause(text: str, number: str) -> bool:
    """True when the text contains exactly this clause number: "1.21" matches "1.21 Students"
    and "1.21.3", but not "11.21" or "1.215"."""
    return re.search(rf"(?<![\d.]){re.escape(number)}(?!\d)", text) is not None


class ChromaRetriever:
    name = "chroma"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None             # created on first use (see _get_client)
        self._old_years: set[str] | None = None

    def retrieve(self, question: str, k: int) -> list[Passage]:
        """Return up to k passages, best first.

        Ordinary questions search current documents only. A question that asks about an
        older edition or a change gets the best passages from both editions, and every
        superseded passage is labelled as such in its text.

        A clause the question names ("clause 1.21") is also looked up as exact text, and
        the chunks that really contain it come first. Every score stays the true cosine
        similarity, so the list is in that order: exact clause matches, then by score."""
        collection, dimension = self._open_collection()
        vector = self._embed_question(question, dimension)
        history = self._asks_about_history(question, collection)
        exact = self._exact_clause_matches(collection, vector, question, history)
        if not history:
            ranked = self._search(collection, vector, k, superseded=False)
        else:
            current = self._search(collection, vector, k, superseded=False)
            old = self._search(collection, vector, k, superseded=True)
            ranked = sorted(current + old, key=lambda p: p.score, reverse=True)[:k]
            # A comparison needs both editions: if one is missing from the top k, its best
            # passage takes the last place.
            for side in (current, old):
                if side and k > 1 and not any(p in ranked for p in side):
                    ranked[-1] = side[0]
            ranked.sort(key=lambda p: p.score, reverse=True)
        found = {p.chunk_id for p in exact}
        return (exact + [p for p in ranked if p.chunk_id not in found])[:k]

    def cache_key(self, question: str) -> tuple[list[float], str]:
        """The question's vector and the current index version (the Chroma collection id),
        for the semantic cache. A re-index makes a new collection, so a new id. The
        embedding pin is checked here too."""
        collection, dimension = self._open_collection()
        return self._embed_question(question, dimension), str(collection.id)

    def check_index(self) -> dict[str, object]:
        """The readiness checks: Chroma answers, the collection exists and is not empty,
        it was built with EMBED_MODEL, and its stored vectors have the recorded length.
        Raises RuntimeError naming the first check that failed."""
        client = self._get_client()
        try:
            client.heartbeat()
        except Exception as exc:
            raise RuntimeError(f"chroma unreachable: {type(exc).__name__}") from exc
        collection, dimension = self._open_collection()
        rows = int(collection.count())
        if rows == 0:
            raise RuntimeError(f"collection {self.settings.collection!r} is empty; "
                               "run the indexer Job")
        sample = collection.get(limit=1, include=["embeddings"])["embeddings"]
        if sample is None or len(sample) == 0 or len(sample[0]) != dimension:
            found = len(sample[0]) if sample is not None and len(sample) else "no"
            raise RuntimeError(f"collection says dimension {dimension}, but a stored "
                               f"vector has {found} values")
        return {"collection": self.settings.collection, "rows": rows,
                "embed_model": self.settings.embed_model, "dimension": dimension}

    def _get_client(self) -> Any:
        """chromadb.HttpClient contacts the server as soon as it is created, so it is made
        on first use: a missing Chroma then makes the pod not-ready instead of crashing it."""
        if self._client is None:
            import chromadb  # the thin client, from chromadb-client in requirements.txt
            from chromadb.config import Settings as ChromaSettings

            try:
                self._client = chromadb.HttpClient(
                    host=self.settings.chroma_host, port=self.settings.chroma_port,
                    settings=ChromaSettings(anonymized_telemetry=False))
            except Exception as exc:
                raise RuntimeError(f"chroma unreachable: {type(exc).__name__}") from exc
        return self._client

    def _open_collection(self) -> tuple[Any, int]:
        """Open the collection and enforce the embedding pin (section 4.4)."""
        name = self.settings.collection
        client = self._get_client()
        try:
            collection = client.get_collection(name)
        except Exception as exc:
            raise RuntimeError(f"collection {name!r} not found or chroma unreachable: "
                               f"{type(exc).__name__}") from exc
        metadata = collection.metadata or {}
        model, dimension = metadata.get("embed_model"), metadata.get("dimension")
        if model != self.settings.embed_model:
            raise RuntimeError(f"embedding model mismatch: the index was built with {model!r} "
                               f"but EMBED_MODEL is {self.settings.embed_model!r}")
        if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1:
            raise RuntimeError(f"collection metadata has no valid dimension (got {dimension!r})")
        return collection, dimension

    def _embed_question(self, question: str, dimension: int) -> list[float]:
        """POST {ollama_url}/api/embed with the pinned model, exactly as the indexer does.
        Never falls back to a fake vector."""
        url = self.settings.ollama_url.rstrip("/") + "/api/embed"
        try:
            response = httpx.post(url, json={"model": self.settings.embed_model,
                                             "input": [question], "truncate": False},
                                  timeout=httpx.Timeout(EMBED_TIMEOUT_SECONDS,
                                                        connect=CONNECT_SECONDS))
        except httpx.HTTPError as exc:
            raise RuntimeError(f"embedding service unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise RuntimeError(f"embedding failed: HTTP {response.status_code} "
                               f"{response.text[:200]}")
        try:
            vector = response.json()["embeddings"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("embedding response has no vector") from exc
        if not isinstance(vector, list) or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in vector):
            raise RuntimeError("embedding response is not a list of finite numbers")
        if len(vector) != dimension:
            raise RuntimeError(f"question vector has {len(vector)} values but the index "
                               f"has {dimension}: not the model that built the index")
        return vector

    def _asks_about_history(self, question: str, collection: Any) -> bool:
        """True when the question asks about an older policy or a change: a history word
        ("previous", "changed", ...) or the year of a superseded edition ("2023"). The
        years come from the stored doc_version metadata, never from a hard-coded list."""
        if HISTORY_WORDS.search(question):
            return True
        if self._old_years is None:
            rows = collection.get(where={"superseded": True}, include=["metadatas"])
            self._old_years = {str(m.get("doc_version", ""))[:4]
                               for m in rows["metadatas"] or []}
        return any(year in self._old_years for year in YEAR.findall(question))

    def _exact_clause_matches(self, collection: Any, vector: list[float], question: str,
                              history: bool) -> list[Passage]:
        """For each clause the question names, the chunks whose text contains that exact
        number (Chroma's $contains text filter), closest first, at most EXACT_PER_CLAUSE.
        The same superseded rule applies as for the vector search."""
        matches: list[Passage] = []
        for number in clause_refs(question):
            # $contains "1.21" also matches "11.21", so fetch a few extra and keep only
            # real matches.
            found = self._search(collection, vector, 3 * EXACT_PER_CLAUSE,
                                 superseded=None if history else False, contains=number)
            real = [p for p in found if mentions_clause(p.text, number)
                    and p.chunk_id not in {m.chunk_id for m in matches}]
            matches += real[:EXACT_PER_CLAUSE]
        return matches

    def _search(self, collection: Any, vector: list[float], k: int, superseded: bool | None,
                contains: str | None = None) -> list[Passage]:
        """The k closest chunks. superseded=None searches both editions; `contains` keeps
        only chunks whose text contains that string."""
        filters: dict[str, Any] = {}
        if superseded is not None:
            filters["where"] = {"superseded": superseded}
        if contains is not None:
            filters["where_document"] = {"$contains": contains}
        found = collection.query(query_embeddings=[vector], n_results=k,
                                 include=["documents", "metadatas", "distances"], **filters)
        passages = []
        for chunk_id, text, meta, distance in zip(found["ids"][0], found["documents"][0],
                                                  found["metadatas"][0], found["distances"][0]):
            if meta.get("superseded"):
                # Outdated evidence is never shown as a current rule.
                text = (f"[SUPERSEDED: {meta.get('doc_version')} edition, not the current "
                        f"rule]\n{text}")
            passages.append(Passage(
                chunk_id=chunk_id, text=text, source=str(meta.get("source", "")),
                section=str(meta.get("section", "")), page=meta.get("page"),
                score=1 - distance,   # Chroma gives cosine distance; similarity = 1 - distance
            ))
        return passages   # Chroma already returns them closest first

"""Semantic cache in Redis (section 3.3).

A new question is embedded with the same model as the index. If an earlier question was
close enough (cosine similarity >= CACHE_THRESHOLD) AND names the same numbers, grades and
codes AND asks about history in the same way, its stored answer is reused. Otherwise the
normal pipeline runs, and a safe answer is stored for next time.

Every entry lives under  rahbar:cache:<embedding model>:<Chroma collection id>:<hash>.
A re-index creates a new collection id, so answers from an old index can never be found
again, and each entry also expires after CACHE_TTL_SECONDS.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from typing import Any

from ..retrieval.chroma import HISTORY_WORDS
from ..schemas import AskResponse

log = logging.getLogger("rahbar.cache")

PREFIX = "rahbar:cache"
# Words where one small difference changes the answer completely: numbers ("15" vs "30"
# days, clause "1.21" vs "5.4", "2023"), letter grades ("B-" vs "B+") and short capital
# codes ("BS" vs "MS", "LHR" vs "ISB"). Two questions must name exactly the same ones.
KEY_TERMS = re.compile(r"\d+(?:\.\d+)*|\b[A-F][+-](?![\w+-])|\b(?:[A-Z]{2,5}|Ph\.?D)\b")
UNIVERSITY_NAME = {"FAST", "NUCES"}   # every question is about this university


def key_terms(question: str) -> list[object]:
    """What two questions must share to use one cache entry: the same key terms, and both
    or neither asking about an older policy ("old", "former", "changed", ...)."""
    terms = sorted({t for t in KEY_TERMS.findall(question) if t not in UNIVERSITY_NAME})
    return [terms, bool(HISTORY_WORDS.search(question))]


def namespace(embed_model: str, index_version: str) -> str:
    """Different embedding models or indexes never share cache entries."""
    return f"{PREFIX}:{embed_model}:{index_version}"


def lookup(redis: Any, space: str, question: str, vector: list[float],
           threshold: float) -> tuple[dict[str, Any] | None, float]:
    """The stored entry most similar to this question that passes the threshold and the key
    term check, with its similarity, or (None, best similarity seen)."""
    keys = list(redis.scan_iter(match=f"{space}:*", count=500))
    best: dict[str, Any] | None = None
    best_score = 0.0
    wanted = key_terms(question)
    for raw in redis.mget(keys) if keys else []:
        if raw is None:              # expired between the scan and the read
            continue
        entry = json.loads(raw)
        score = _cosine(vector, entry["vector"])
        if entry["key_terms"] == wanted and score >= threshold and score > best_score:
            best, best_score = entry, score
    return best, best_score


def store(redis: Any, space: str, question: str, vector: list[float],
          response: AskResponse, ttl_seconds: int) -> None:
    """Save an answer for reuse. The request id is not stored (every request gets its own),
    and the model's reasoning never reaches AskResponse, so it is not stored either."""
    entry = {"question": question, "vector": vector, "key_terms": key_terms(question),
             "response": response.model_dump(exclude={"request_id"})}
    normalised = " ".join(question.lower().split())
    key = f"{space}:{hashlib.sha256(normalised.encode()).hexdigest()[:16]}"
    redis.set(key, json.dumps(entry), ex=ttl_seconds)


def worth_caching(response: AskResponse) -> bool:
    """Only answers given from the documents, which passed the citation check, are stored.
    Abstentions, the safe fallback and errors are never stored: a cached mistake would be
    served to everyone (section 3.3)."""
    return response.answered_from_context and bool(response.citations)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0.0

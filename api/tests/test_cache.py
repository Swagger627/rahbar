"""Semantic cache tests. A tiny in-memory Redis (with a clock, for expiry) and a fake
embedding function stand in for the real services, so the vectors here are test data
chosen to be similar or different on purpose."""

from __future__ import annotations

import fnmatch
import json
import math
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.logjson import request_id_var
from app.retrieval import chroma as chroma_module
from app.retrieval.chroma import ChromaRetriever
from app.retrieval.stub import StubRetriever
from app.schemas import Answer, Citation, Passage
from app.services import cache
from app.services.answer import answer_with_cache
from app.services.grounding import NOT_COVERED
from tests.test_retrieval import ROWS, FakeClient, FakeCollection

SETTINGS = replace(get_settings(), retriever="chroma", embed_model="nomic-embed-text",
                   ollama_url="http://ollama.test:11434", relevance_floor=0.0,
                   cache_threshold=0.90, cache_ttl_seconds=3600)


class FakeRedis:
    """get/set with expiry, scan_iter and mget: all the cache uses."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[str, float]] = {}
        self.now = 0.0

    def set(self, key: str, value: str, ex: int) -> None:
        self.data[key] = (value, self.now + ex)

    def scan_iter(self, match: str, count: int) -> list[str]:
        return [k for k, (_, until) in self.data.items()
                if fnmatch.fnmatch(k, match) and until > self.now]

    def mget(self, keys: list[str]) -> list[str | None]:
        return [self.data[k][0] if k in self.data and self.data[k][1] > self.now else None
                for k in keys]


class BrokenRedis:
    def scan_iter(self, match: str, count: int) -> list[str]:
        raise ConnectionError("redis is down")


class CountingProvider:
    """Answers from the first passage, like the stub, and counts its calls."""
    name = "counting"

    def __init__(self, answered: bool = True) -> None:
        self.calls, self.answered = 0, answered

    def generate(self, question: str, passages: list[Passage],
                 problem: str | None = None) -> Answer:
        self.calls += 1
        top = passages[0]
        if not self.answered:
            return Answer(reasoning="SECRET-REASONING", answer="no", citations=[],
                          answered_from_context=False, confidence="low")
        return Answer(reasoning="SECRET-REASONING", answer=f"From {top.section}.",
                      citations=[Citation(source=top.source, section=top.section, page=top.page)],
                      answered_from_context=True, confidence="high")


def angle_vector(degrees: float) -> list[float]:
    """A 3-d unit vector; two of them have cosine similarity cos(angle difference)."""
    r = math.radians(degrees)
    return [math.cos(r), math.sin(r), 0.0]


SAME = 0.0
CLOSE = math.degrees(math.acos(0.95))    # similarity 0.95: above the 0.90 threshold
FAR = math.degrees(math.acos(0.85))      # similarity 0.85: below it


def setup(monkeypatch: pytest.MonkeyPatch, angles: dict[str, float],
          index_id: str = "index-1") -> ChromaRetriever:
    """A ChromaRetriever on the fake collection, whose question vectors come from `angles`."""
    def post(url: str, json: dict[str, Any], timeout: Any) -> httpx.Response:
        vector = angle_vector(angles[json["input"][0]])
        return httpx.Response(200, json={"embeddings": [vector]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(chroma_module.httpx, "post", post)
    retriever = ChromaRetriever(SETTINGS)
    collection = FakeCollection(ROWS, {"embed_model": "nomic-embed-text", "dimension": 3})
    collection.id = index_id                       # type: ignore[attr-defined]
    retriever._client = FakeClient(collection)
    return retriever


def ask(question: str, retriever: Any, provider: Any, redis: Any,
        request_id: str = "req") -> Any:
    token = request_id_var.set(request_id)
    try:
        return answer_with_cache(question, retriever, provider, SETTINGS, redis)
    finally:
        request_id_var.reset(token)


Q = "What is the attendance requirement?"


def test_first_question_is_a_miss_and_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    redis, provider = FakeRedis(), CountingProvider()
    response, result = ask(Q, setup(monkeypatch, {Q: SAME}), provider, redis)
    assert result == "miss" and provider.calls == 1 and len(redis.data) == 1
    stored = next(iter(redis.data))
    assert stored.startswith("rahbar:cache:nomic-embed-text:index-1:")


def test_exact_repeat_is_a_hit_with_a_fresh_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    redis, provider = FakeRedis(), CountingProvider()
    retriever = setup(monkeypatch, {Q: SAME})
    first, _ = ask(Q, retriever, provider, redis, request_id="first")
    second, result = ask(Q, retriever, provider, redis, request_id="second")
    assert result == "hit" and provider.calls == 1             # the model was not asked again
    assert first.request_id == "first" and second.request_id == "second"
    assert second.answer == first.answer and second.citations == first.citations


def test_hit_citations_still_match_its_retrieved_passages(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    retriever = setup(monkeypatch, {Q: SAME})
    ask(Q, retriever, CountingProvider(), redis)
    hit, _ = ask(Q, retriever, CountingProvider(), redis)
    retrieved = {(r.source, r.section, r.page) for r in hit.retrieved}
    assert hit.citations and all((c.source, c.section, c.page) in retrieved for c in hit.citations)


def test_a_close_paraphrase_is_a_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    redis, provider = FakeRedis(), CountingProvider()
    p = "How much attendance is required?"
    retriever = setup(monkeypatch, {Q: SAME, p: CLOSE})
    ask(Q, retriever, provider, redis)
    _, result = ask(p, retriever, provider, redis)
    assert result == "hit" and provider.calls == 1


def test_below_the_threshold_is_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    redis, provider = FakeRedis(), CountingProvider()
    p = "What happens if attendance falls below the minimum?"
    retriever = setup(monkeypatch, {Q: SAME, p: FAR})
    ask(Q, retriever, provider, redis)
    _, result = ask(p, retriever, provider, redis)
    assert result == "miss" and provider.calls == 2


@pytest.mark.parametrize("first, second", [
    ("What percentage of tuition is refunded within 15 days?",
     "What percentage of tuition is refunded within 30 days?"),        # different numbers
    ("What does clause 1.21 require?", "What does clause 5.4 require?"),   # different clauses
    ("How many grade points is a B- worth?", "How many grade points is a B+ worth?"),
    ("Which campus offers the MS in Data Science?", "Which campus offers the BS in Data Science?"),
    ("What is the current rule on repeating a failed course?",
     "What was the old rule on repeating a failed course?"),           # history
    ("When must I repeat a failed course?",
     "Under the 2023 rules, when did a student have to repeat a failed course?"),
])
def test_same_vector_but_different_meaning_is_a_miss(monkeypatch: pytest.MonkeyPatch,
                                                     first: str, second: str) -> None:
    # Even with IDENTICAL vectors, the key-term check keeps these apart (the real model
    # scored several of these pairs 0.93-0.96, above most genuine paraphrases).
    redis, provider = FakeRedis(), CountingProvider()
    retriever = setup(monkeypatch, {first: SAME, second: SAME})
    ask(first, retriever, provider, redis)
    _, result = ask(second, retriever, provider, redis)
    assert result == "miss" and provider.calls == 2


def test_university_name_does_not_block_a_paraphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    redis, provider = FakeRedis(), CountingProvider()
    a, b = "Which campuses have hostels?", "Which FAST-NUCES campuses have hostels?"
    retriever = setup(monkeypatch, {a: SAME, b: CLOSE})
    ask(a, retriever, provider, redis)
    assert ask(b, retriever, provider, redis)[1] == "hit"


def test_an_expired_entry_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    redis, provider = FakeRedis(), CountingProvider()
    retriever = setup(monkeypatch, {Q: SAME})
    ask(Q, retriever, provider, redis)
    redis.now += SETTINGS.cache_ttl_seconds + 1
    assert ask(Q, retriever, provider, redis)[1] == "miss" and provider.calls == 2


def test_a_reindex_makes_old_answers_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    redis, provider = FakeRedis(), CountingProvider()
    ask(Q, setup(monkeypatch, {Q: SAME}, index_id="index-1"), provider, redis)
    after_reindex = setup(monkeypatch, {Q: SAME}, index_id="index-2")   # new collection id
    assert ask(Q, after_reindex, provider, redis)[1] == "miss" and provider.calls == 2


def test_different_embedding_models_never_share_entries() -> None:
    assert cache.namespace("nomic-embed-text", "index-1") != cache.namespace("other-model", "index-1")


def test_abstentions_are_never_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    response, result = ask(Q, setup(monkeypatch, {Q: SAME}), CountingProvider(answered=False),
                           redis)
    assert result == "miss" and response.answer == NOT_COVERED and redis.data == {}


def test_a_fallback_is_never_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    class DownProvider(CountingProvider):
        def generate(self, question: str, passages: list[Passage],
                     problem: str | None = None) -> Answer:
            raise RuntimeError("LLM unreachable: ConnectError")

    redis = FakeRedis()
    response, _ = ask(Q, setup(monkeypatch, {Q: SAME}), DownProvider(), redis)
    assert response.answered_from_context is False and redis.data == {}


def test_reasoning_and_request_id_are_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    ask(Q, setup(monkeypatch, {Q: SAME}), CountingProvider(), redis, request_id="req-123")
    stored = next(iter(redis.data.values()))[0]
    assert "SECRET-REASONING" not in stored and "req-123" not in stored
    assert json.loads(stored)["response"]["answered_from_context"] is True


def test_redis_down_runs_the_normal_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = CountingProvider()
    response, result = ask(Q, setup(monkeypatch, {Q: SAME}), provider, BrokenRedis())
    assert result == "error" and provider.calls == 1 and response.answered_from_context


def test_no_redis_or_the_stub_retriever_means_cache_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ask(Q, setup(monkeypatch, {Q: SAME}), CountingProvider(), None)[1] == "off"
    assert ask(Q, StubRetriever(), CountingProvider(), FakeRedis())[1] == "off"


def test_the_route_reports_the_cache_result_in_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("RETRIEVER", "stub")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.delenv("REDIS_URL", raising=False)
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/api/ask", json={"question": Q})
    assert r.status_code == 200 and r.headers["X-Cache"] == "off"
    assert set(r.json()) == {"request_id", "answer", "citations", "answered_from_context",
                             "confidence", "retrieved", "provider"}     # contract unchanged

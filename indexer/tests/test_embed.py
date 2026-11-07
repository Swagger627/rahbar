"""Embedding tests with controlled HTTP responses: no Ollama, no model. The fake
replaces httpx.post, so these vectors are test data, never real embeddings."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx
import pytest

from pipeline import embed as embed_module
from pipeline.config import get_settings
from pipeline.embed import BATCH_SIZE, embed

SETTINGS = replace(get_settings(), embed_model="nomic-embed-text",
                   ollama_url="http://ollama.test:11434/")


def fake_ollama(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[dict[str, Any]]:
    """Make httpx.post return `answer(request_json)`: a (status, body) pair, or an exception
    to raise. Returns the list of requests that were sent."""
    sent: list[dict[str, Any]] = []

    def post(url: str, json: dict[str, Any], timeout: Any) -> httpx.Response:
        sent.append({"url": url, **json})
        result = answer(json)
        if isinstance(result, Exception):
            raise result
        status, body = result
        kwargs = {"json": body} if not isinstance(body, str) else {"text": body}
        return httpx.Response(status, request=httpx.Request("POST", url), **kwargs)

    monkeypatch.setattr(embed_module.httpx, "post", post)
    return sent


def vectors_for(request: dict[str, Any], dim: int = 4) -> tuple[int, dict[str, Any]]:
    return 200, {"embeddings": [[0.1] * dim for _ in request["input"]]}


def test_texts_are_sent_in_batches_to_the_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = fake_ollama(monkeypatch, vectors_for)
    texts = [f"chunk {i}" for i in range(BATCH_SIZE * 2 + 3)]
    vectors = embed(texts, SETTINGS)
    assert len(vectors) == len(texts) and all(len(v) == 4 for v in vectors)
    assert [len(r["input"]) for r in sent] == [BATCH_SIZE, BATCH_SIZE, 3]
    assert {r["url"] for r in sent} == {"http://ollama.test:11434/api/embed"}
    assert {r["model"] for r in sent} == {"nomic-embed-text"}
    assert all(r["truncate"] is False for r in sent)
    assert sent[1]["input"][0] == f"chunk {BATCH_SIZE}"          # order is kept


def test_no_texts_means_no_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = fake_ollama(monkeypatch, vectors_for)
    assert embed([], SETTINGS) == [] and sent == []


def test_ollama_not_running_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ollama(monkeypatch, lambda request: httpx.ConnectError("connection refused"))
    with pytest.raises(RuntimeError, match="nomic-embed-text.*ollama.test.*connection refused"):
        embed(["a"], SETTINGS)


def test_missing_model_reports_ollamas_message(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"error": 'model "nomic-embed-text" not found, try pulling it first'}
    fake_ollama(monkeypatch, lambda request: (404, body))
    with pytest.raises(RuntimeError, match="HTTP 404.*try pulling it first"):
        embed(["a"], SETTINGS)


@pytest.mark.parametrize("status, body, message", [
    (200, "not json", "no 'embeddings' list"),
    (200, {"something": "else"}, "no 'embeddings' list"),
    (200, {"embeddings": "nope"}, "got no vectors"),
    (200, {"embeddings": [[0.1, 0.2]]}, "sent 2 texts, got 1 vectors"),
    (200, {"embeddings": [[0.1, 0.2], []]}, "embedding 1 is empty"),
    (200, {"embeddings": [[0.1, 0.2], [0.1, 0.2, 0.3]]}, "dimension 3, but embedding 0 has 2"),
    (200, {"embeddings": [[0.1, 0.2], [0.1, "x"]]}, "not a finite number"),
    (200, {"embeddings": [[0.1, 0.2], [0.1, None]]}, "not a finite number"),
])
def test_malformed_responses_are_rejected(monkeypatch: pytest.MonkeyPatch, status: int,
                                          body: Any, message: str) -> None:
    fake_ollama(monkeypatch, lambda request: (status, body))
    with pytest.raises(RuntimeError, match=message):
        embed(["a", "b"], SETTINGS)


def test_dimension_must_match_across_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = iter([4, 8])
    fake_ollama(monkeypatch, lambda request: vectors_for(request, dim=next(calls)))
    with pytest.raises(RuntimeError, match="dimension 8, but embedding 0 has 4"):
        embed(["x"] * (BATCH_SIZE + 1), SETTINGS)


def test_the_dummy_model_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = fake_ollama(monkeypatch, vectors_for)
    with pytest.raises(RuntimeError, match="dummy-768"):
        embed(["a"], replace(SETTINGS, embed_model="dummy-768"))
    assert sent == []

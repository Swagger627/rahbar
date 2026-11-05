"""The section 4.8 metrics: they exist on /metrics and move when an answer is produced.
Model replies here are TEST DATA (a fake httpx.post), not real model behaviour."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.providers.llm import ollama as ollama_module
from app.services.answer import answer_question
from test_generation import ATTENDANCE, SETTINGS, FakeRetriever, reply


def value(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def fake_reply(monkeypatch: pytest.MonkeyPatch, content: str, tokens_in: int, tokens_out: int) -> None:
    def post(url: str, json: dict[str, Any], timeout: Any) -> httpx.Response:
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "message": {"role": "assistant", "content": content},
            "prompt_eval_count": tokens_in, "eval_count": tokens_out})
    monkeypatch.setattr(ollama_module.httpx, "post", post)


def test_metrics_endpoint_lists_every_section_4_8_metric() -> None:
    from app.main import app
    with TestClient(app) as client:
        client.post("/api/ask", json={"question": "What is the attendance rule?"})
        text = client.get("/metrics").text
    for name in ("rahbar_requests_total", "rahbar_request_seconds", "rahbar_retrieval_seconds",
                 "rahbar_generation_seconds", "rahbar_cache_lookups_total", "rahbar_answers_total",
                 "rahbar_generation_retries_total", "rahbar_llm_tokens_total"):
        assert name in text, name


def test_answer_counts_outcome_latency_and_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.llm.ollama import OllamaProvider
    fake_reply(monkeypatch, reply(), tokens_in=812, tokens_out=37)
    answered = value("rahbar_answers_total", {"outcome": "answered"})
    tokens_in = value("rahbar_llm_tokens_total", {"direction": "in"})
    tokens_out = value("rahbar_llm_tokens_total", {"direction": "out"})
    retrievals = value("rahbar_retrieval_seconds_count")
    generations = value("rahbar_generation_seconds_count")

    answer_question("What is the attendance rule?", FakeRetriever([ATTENDANCE]),
                    OllamaProvider(SETTINGS), SETTINGS)

    assert value("rahbar_answers_total", {"outcome": "answered"}) == answered + 1
    assert value("rahbar_llm_tokens_total", {"direction": "in"}) == tokens_in + 812
    assert value("rahbar_llm_tokens_total", {"direction": "out"}) == tokens_out + 37
    assert value("rahbar_retrieval_seconds_count") == retrievals + 1
    assert value("rahbar_generation_seconds_count") == generations + 1


def test_abstention_without_evidence_is_counted_and_calls_no_model(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.llm.ollama import OllamaProvider
    abstained = value("rahbar_answers_total", {"outcome": "abstained"})
    generations = value("rahbar_generation_seconds_count")
    answer_question("hostel fee?", FakeRetriever([]), OllamaProvider(SETTINGS), SETTINGS)
    assert value("rahbar_answers_total", {"outcome": "abstained"}) == abstained + 1
    assert value("rahbar_generation_seconds_count") == generations   # no model call


def test_twice_rejected_reply_counts_a_retry_and_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.llm.ollama import OllamaProvider
    fake_reply(monkeypatch, json.dumps({"answer": "not an Answer"}), 10, 2)
    fallbacks = value("rahbar_answers_total", {"outcome": "fallback"})
    answer_question("What is the attendance rule?", FakeRetriever([ATTENDANCE]),
                    OllamaProvider(SETTINGS), SETTINGS)
    assert value("rahbar_answers_total", {"outcome": "fallback"}) == fallbacks + 1

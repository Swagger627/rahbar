"""Hosted (OpenAI-compatible) provider tests. httpx.post is replaced by a fake that plays
back chosen replies, so no network and no key are needed. The replies are TEST DATA: they
show how our code handles a reply, not how any real model behaves."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.providers.llm import get_provider
from app.providers.llm import hosted as hosted_module
from app.providers.llm.hosted import HostedProvider
from app.schemas import Passage

FAKE_KEY = "test-key-not-a-real-credential"
SETTINGS = replace(get_settings(), llm_provider="hosted",
                   hosted_base_url="https://llm.example.test/v1/", llm_api_key=FAKE_KEY,
                   llm_model="some-model", temperature=0.2)
PASSAGE = Passage(chunk_id="rules-0010", text="1.21 Students must attend 80% of classes.",
                  source="rules.pdf", section="Attendance", page=10, score=0.8)
GOOD = {"reasoning": "passage 1", "answer": "80% [rules.pdf / Attendance].",
        "citations": [{"source": "rules.pdf", "section": "Attendance", "page": 10}],
        "answered_from_context": True, "confidence": "high"}


def fake_post(monkeypatch: pytest.MonkeyPatch, status: int, body: Any) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        sent.append({"url": url, **kwargs})
        content = body if isinstance(body, str) else json.dumps(body)
        return httpx.Response(status, content=content.encode())

    monkeypatch.setattr(hosted_module.httpx, "post", post)
    return sent


def chat_reply(answer: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(answer)}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 40}}


def test_selected_by_llm_provider_hosted() -> None:
    assert isinstance(get_provider(SETTINGS), HostedProvider)


def test_request_goes_to_chat_completions_with_bearer_key_and_json_mode(
        monkeypatch: pytest.MonkeyPatch) -> None:
    sent = fake_post(monkeypatch, 200, chat_reply(GOOD))
    answer = HostedProvider(SETTINGS).generate("What attendance is needed?", [PASSAGE])
    assert answer.answer.startswith("80%")
    request = sent[0]
    assert request["url"] == "https://llm.example.test/v1/chat/completions"
    assert request["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert request["json"]["model"] == "some-model"
    assert request["json"]["temperature"] == 0.2
    assert request["json"]["response_format"] == {"type": "json_object"}
    # the same prompt as the Ollama provider: system rules, then the evidence
    assert request["json"]["messages"][0]["role"] == "system"
    assert "<evidence>" in request["json"]["messages"][1]["content"]
    # JSON mode has no schema, so the Answer schema is spelled out in the instructions
    assert '"answered_from_context"' in request["json"]["messages"][0]["content"]


def test_retry_adds_the_problem_and_the_allowed_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = fake_post(monkeypatch, 200, chat_reply(GOOD))
    HostedProvider(SETTINGS).generate("q?", [PASSAGE], problem="a citation was not retrieved")
    last = sent[0]["json"]["messages"][-1]["content"]
    assert "a citation was not retrieved" in last and "rules.pdf" in last


def test_reply_that_is_not_an_answer_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_post(monkeypatch, 200, chat_reply({"answer": "missing fields"}))
    with pytest.raises(ValueError):
        HostedProvider(SETTINGS).generate("q?", [PASSAGE])


@pytest.mark.parametrize(("status", "words"), [(401, "refused the key"), (403, "refused the key"),
                                               (429, "rate limit"), (500, "HTTP 500")])
def test_http_errors_raise_runtime_error_without_the_key(
        monkeypatch: pytest.MonkeyPatch, status: int, words: str) -> None:
    fake_post(monkeypatch, status, {"error": {"message": f"bad key {FAKE_KEY}"}})
    with pytest.raises(RuntimeError) as err:
        HostedProvider(SETTINGS).generate("q?", [PASSAGE])
    assert words in str(err.value)
    assert FAKE_KEY not in str(err.value)      # never echo the key or the body


def test_unreachable_endpoint_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def post(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(hosted_module.httpx, "post", post)
    with pytest.raises(RuntimeError, match="unreachable"):
        HostedProvider(SETTINGS).generate("q?", [PASSAGE])


@pytest.mark.parametrize("missing", ["hosted_base_url", "llm_api_key"])
def test_missing_configuration_fails_at_start_up(missing: str) -> None:
    with pytest.raises(RuntimeError) as err:
        HostedProvider(replace(SETTINGS, **{missing: ""}))
    assert FAKE_KEY not in str(err.value)

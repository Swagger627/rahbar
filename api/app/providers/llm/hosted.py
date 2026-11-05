"""Hosted provider: any OpenAI-compatible chat-completions endpoint (section 3.5).

Groq, OpenRouter and Google AI Studio's OpenAI-compatible endpoint all accept
POST {HOSTED_BASE_URL}/chat/completions with a Bearer key. RAHBAR's default mode is
ollama-host; this mode exists so that swapping the generator is configuration, not code
(docs/adr/0004-llm-provider-and-data-governance.md says what leaves the cluster).

- The key comes only from LLM_API_KEY, which the Deployment reads from the Secret
  rahbar-secret (created on the command line, never committed). It is never logged and
  never put in an error message.
- The prompt and the validation are exactly the Ollama provider's: same rules, same
  evidence format, same Answer contract, same single retry.
"""

from __future__ import annotations

import json
import logging

import httpx

from ...config import Settings
from ...schemas import Answer, Passage
from .ollama import build_messages, count_tokens, parse_answer, retry_message

log = logging.getLogger("rahbar.llm")

GENERATE_TIMEOUT_SECONDS = 60
CONNECT_SECONDS = 5
SCHEMA_NOTE = ("\nThe JSON object must match this JSON schema exactly (every field required; "
               "each citation is an object with source, section and page):\n"
               + json.dumps(Answer.model_json_schema()))


class HostedProvider:
    name = "hosted"

    def __init__(self, settings: Settings) -> None:
        # Fail at start-up, loudly, rather than on the first question.
        if not settings.hosted_base_url:
            raise RuntimeError("LLM_PROVIDER=hosted needs HOSTED_BASE_URL in the ConfigMap")
        if not settings.llm_api_key:
            raise RuntimeError("LLM_PROVIDER=hosted needs LLM_API_KEY from the Secret rahbar-secret")
        self.settings = settings

    def generate(self, question: str, passages: list[Passage],
                 problem: str | None = None) -> Answer:
        """Same contract as OllamaProvider.generate: ValueError for a reply that is not a
        valid Answer (the caller may retry once), RuntimeError when the endpoint fails."""
        messages = build_messages(question, passages)
        # JSON mode only promises "some JSON object". Ollama's native API gets the schema
        # through format=; here it goes into the instructions instead. (A real test against
        # an OpenAI-compatible endpoint without it returned citations as plain strings.)
        messages[0] = {"role": "system", "content": messages[0]["content"] + SCHEMA_NOTE}
        if problem:
            messages.append(retry_message(problem, passages))
        url = self.settings.hosted_base_url.rstrip("/") + "/chat/completions"
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                json={
                    "model": self.settings.llm_model,
                    "messages": messages,
                    "temperature": self.settings.temperature,
                    "response_format": {"type": "json_object"},   # JSON only; we validate it
                },
                timeout=httpx.Timeout(GENERATE_TIMEOUT_SECONDS, connect=CONNECT_SECONDS))
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LLM unreachable: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise RuntimeError(f"LLM refused the key: HTTP {response.status_code}")
        if response.status_code == 429:
            raise RuntimeError("LLM rate limit reached: HTTP 429")
        if response.status_code != 200:
            raise RuntimeError(f"LLM failed: HTTP {response.status_code}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM reply has no message content") from exc
        usage = data.get("usage") or {}
        count_tokens(usage.get("prompt_tokens"), usage.get("completion_tokens"))
        log.info("llm reply", extra={"model": self.settings.llm_model,
                                     "prompt_tokens": usage.get("prompt_tokens"),
                                     "output_tokens": usage.get("completion_tokens")})
        return parse_answer(content)

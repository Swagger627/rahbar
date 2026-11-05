"""Prometheus metrics at /metrics (section 4.8).

- request count and latency:  rahbar_requests_total, rahbar_request_seconds
- retrieval latency:          rahbar_retrieval_seconds
- generation latency:         rahbar_generation_seconds (the model call, with its one retry)
- cache hit ratio:            rahbar_cache_lookups_total{result="hit"} / all lookups
- abstentions and fallbacks:  rahbar_answers_total{outcome="abstained" | "fallback" | "answered"}
- retries:                    rahbar_generation_retries_total
- tokens in and out:          rahbar_llm_tokens_total{direction="in" | "out"}
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

router = APIRouter()

REQUESTS = Counter("rahbar_requests_total", "HTTP requests", ["path", "status"])
# result = hit | miss | error (Redis failed, the request went on without the cache)
CACHE = Counter("rahbar_cache_lookups_total", "Semantic cache lookups", ["result"])
LATENCY = Histogram(
    "rahbar_request_seconds",
    "HTTP request latency",
    ["path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
RETRIEVAL = Histogram("rahbar_retrieval_seconds", "Embedding the question and searching Chroma",
                      buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5))
GENERATION = Histogram("rahbar_generation_seconds", "The model call(s) for one answer",
                       buckets=(0.25, 0.5, 1, 2, 5, 10, 30, 60, 120))
# outcome = answered | abstained | fallback (the safe refusal after a failed or twice-rejected reply)
ANSWERS = Counter("rahbar_answers_total", "Answers by outcome", ["outcome"])
RETRIES = Counter("rahbar_generation_retries_total", "Model replies rejected once and retried")
TOKENS = Counter("rahbar_llm_tokens_total", "LLM tokens", ["direction"])


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

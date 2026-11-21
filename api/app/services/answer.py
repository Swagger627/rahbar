"""Turns a question into an AskResponse. This is where the query pipeline lives.

semantic cache -> retrieve -> relevance floor -> drop passages that instruct the AI ->
generate -> check citations (one retry) -> respond -> store a safe answer in the cache.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .. import trace
from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..retrieval.chroma import ChromaRetriever
from ..routes.metrics import ANSWERS, CACHE, GENERATION, RETRIES, RETRIEVAL
from ..schemas import Answer, AskResponse, Passage, RetrievedRef
from . import cache
from .grounding import (FALLBACK, NOT_COVERED, above_floor, check_answer, refusal,
                        without_instructions)

log = logging.getLogger("rahbar.answer")


def answer_with_cache(question: str, retriever: Retriever, provider: LLMProvider,
                      settings: Settings, redis: Any, *,
                      retrieve_only: bool = False) -> tuple[AskResponse, str]:
    """answer_question with the semantic cache around it. Returns the response and the
    cache result: "hit", "miss", "error" (Redis failed, so the cache was skipped) or "off"
    (no Redis configured, or the stub retriever, which has no embeddings).

    Redis problems never fail the request and never serve anything unchecked: the request
    simply runs the normal pipeline."""
    if retrieve_only or redis is None or not isinstance(retriever, ChromaRetriever):
        return answer_question(question, retriever, provider, settings,
                               retrieve_only=retrieve_only), "off"
    vector, index_version = retriever.cache_key(question)
    config = (settings.llm_model, settings.temperature, settings.seed, settings.top_k,
              settings.relevance_floor, settings.active_only)
    space = cache.namespace(settings.embed_model, f"{index_version}:{config!r}")
    cache_started = time.perf_counter()
    try:
        entry, similarity = cache.lookup(redis, space, question, vector,
                                         settings.cache_threshold)
    except Exception as exc:
        trace.stage("cache_lookup", cache_started)
        log.warning("cache lookup failed; continuing without it",
                    extra={"error": type(exc).__name__})
        CACHE.labels(result="error").inc()
        return answer_question(question, retriever, provider, settings), "error"
    trace.stage("cache_lookup", cache_started)
    if entry is not None and (trace.current.get() is None or entry.get("trace_id")):
        CACHE.labels(result="hit").inc()
        log.info("cache hit", extra={"similarity": round(similarity, 4),
                                     "chunk_ids": [r["chunk_id"] for r in entry["response"]["retrieved"]]})
        trace.record(outcome="cache_hit", original_trace_id=entry.get("trace_id"),
                     retrieved=entry["response"]["retrieved"])
        # The stored answer, with this request's own id.
        return AskResponse(**entry["response"], request_id=request_id_var.get()), "hit"
    CACHE.labels(result="miss").inc()
    response = answer_question(question, retriever, provider, settings)
    if cache.worth_caching(response):
        try:
            cache.store(redis, space, question, vector, response, settings.cache_ttl_seconds,
                        trace_id=(trace.current.get() or {}).get("trace_id"))
        except Exception as exc:
            log.warning("cache store failed", extra={"error": type(exc).__name__})
    return response, "miss"


def answer_question(
    question: str, retriever: Retriever, provider: LLMProvider, settings: Settings,
    *, retrieve_only: bool = False,
) -> AskResponse:
    started = time.perf_counter()
    passages = retriever.retrieve(question, k=settings.top_k)
    RETRIEVAL.observe(time.perf_counter() - started)
    trace.stage("retrieval", started)
    started = time.perf_counter()
    relevant = above_floor(passages, settings.relevance_floor, question)
    evidence, dropped = without_instructions(relevant)
    trace.record(retrieved=[p.model_dump(exclude={"text"}) for p in passages],
                 reranked_order=[p.chunk_id for p in passages],
                 context=[p.model_dump() for p in evidence],
                 relevance_floor=settings.relevance_floor)
    for passage in passages:
        if passage not in relevant:
            trace.action(action="drop_below_relevance", chunk_id=passage.chunk_id, score=passage.score)
    for chunk_id in dropped:
        trace.action(action="drop_injected_instruction", chunk_id=chunk_id)
    trace.stage("guardrails", started)
    if dropped:
        log.warning("passages with AI instructions dropped", extra={"chunk_ids": dropped})
    if retrieve_only:
        answer, outcome = refusal(NOT_COVERED, "retrieval-only evaluation"), "retrieval_only"
    elif evidence:
        started = time.perf_counter()
        answer, outcome = generate_checked(question, evidence, provider)
        GENERATION.observe(time.perf_counter() - started)
        trace.stage("generation", started)
    else:
        # Nothing relevant enough: abstain without calling the model at all.
        answer, outcome = refusal(NOT_COVERED, "nothing cleared the relevance floor"), "abstained"
    trace.record(outcome=outcome, outcome_reason=answer.reasoning
                 if not answer.answered_from_context else None)
    if outcome.endswith("after retry"):
        RETRIES.inc()
    ANSWERS.labels(outcome=outcome.split()[0]).inc()     # answered | abstained | fallback

    log.info(
        "answered",
        extra={
            "retriever": retriever.name,
            "provider": provider.name,
            "top_k": settings.top_k,
            "chunk_ids": [p.chunk_id for p in passages],
            "scores": [round(p.score, 4) for p in passages],
            "relevance_floor": settings.relevance_floor,
            "outcome": outcome,
            "answered_from_context": answer.answered_from_context,
        },
    )
    return AskResponse(
        request_id=request_id_var.get(),
        answer=answer.answer,                 # the model's `reasoning` is never sent
        citations=answer.citations,
        answered_from_context=answer.answered_from_context,
        confidence=answer.confidence,
        retrieved=[
            RetrievedRef(
                chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page,
                pages=p.pages, document_id=p.document_id, doc_version=p.doc_version, score=p.score
            )
            for p in passages
        ],
        provider=provider.name,
    )


def generate_checked(question: str, evidence: list[Passage],
                     provider: LLMProvider) -> tuple[Answer, str]:
    """Ask the model, check the reply, and retry ONCE with the problem if it was rejected.
    Returns (answer, outcome); after a second rejection or a failed call, the safe
    FALLBACK refusal. A user never sees a 500 or an unchecked answer."""
    problem: str | None = None
    for attempt in (1, 2):
        try:
            answer = provider.generate(question, evidence, problem)
        except ValueError as exc:            # not valid Answer JSON
            problem = str(exc)
        except RuntimeError as exc:          # model unreachable or timed out: no retry
            log.warning("generation failed", extra={"attempt": attempt, "problem": str(exc)})
            return refusal(FALLBACK, f"generation failed: {exc}"), "fallback"
        else:
            checked_at = time.perf_counter()
            answer, problem = check_answer(answer, evidence)
            trace.stage("citation_validation", checked_at)
            if problem is None:
                outcome = "answered" if answer.answered_from_context else "abstained"
                return answer, outcome if attempt == 1 else f"{outcome} after retry"
        trace.action(action="reject_model_reply", attempt=attempt, reason=problem)
        log.warning("model reply rejected", extra={"attempt": attempt, "problem": problem})
    return refusal(FALLBACK, f"rejected twice: {problem}"), "fallback"

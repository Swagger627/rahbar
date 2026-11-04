"""Turns a question into an AskResponse. This is where the query pipeline lives.

retrieve -> relevance floor -> drop passages that instruct the AI -> generate ->
check citations (one retry) -> respond.
The semantic cache (3.3) is added in a later step.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..schemas import Answer, AskResponse, Passage, RetrievedRef
from .grounding import (FALLBACK, NOT_COVERED, above_floor, check_answer, refusal,
                        without_instructions)

log = logging.getLogger("rahbar.answer")


def answer_question(
    question: str, retriever: Retriever, provider: LLMProvider, settings: Settings
) -> AskResponse:
    passages = retriever.retrieve(question, k=settings.top_k)
    evidence, dropped = without_instructions(above_floor(passages, settings.relevance_floor))
    if dropped:
        log.warning("passages with AI instructions dropped", extra={"chunk_ids": dropped})
    if evidence:
        answer, outcome = generate_checked(question, evidence, provider)
    else:
        # Nothing relevant enough: abstain without calling the model at all.
        answer, outcome = refusal(NOT_COVERED, "nothing cleared the relevance floor"), "abstained"

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
                chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page, score=p.score
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
            answer, problem = check_answer(answer, evidence)
            if problem is None:
                outcome = "answered" if answer.answered_from_context else "abstained"
                return answer, outcome if attempt == 1 else f"{outcome} after retry"
        log.warning("model reply rejected", extra={"attempt": attempt, "problem": problem})
    return refusal(FALLBACK, f"rejected twice: {problem}"), "fallback"

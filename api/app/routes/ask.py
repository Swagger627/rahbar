"""POST /api/ask. The Ingress sends /api/* here unchanged, so the prefix stays."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Response

from .. import trace
from ..logjson import request_id_var
from ..schemas import AskRequest, AskResponse
from ..services.answer import answer_with_cache

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request, response: Response) -> AskResponse:
    state = request.app.state
    started = time.perf_counter()
    entry = (trace.start(state.settings, uuid.uuid4().hex, request_id_var.get(), body.question)
             if state.settings.trace_dir else None)
    token = trace.current.set(entry)
    evaluate = state.settings.evaluation_mode and request.headers.get("X-Rahbar-Eval") == "1"
    retrieve_only = evaluate and request.headers.get("X-Rahbar-Retrieval-Only") == "1"
    trace.record(evaluation=evaluate, retrieval_only=retrieve_only)
    try:
        result, cache_result = answer_with_cache(
            body.question, state.retriever, state.provider, state.settings,
            None if evaluate else state.redis, retrieve_only=retrieve_only,
        )
        response.headers["X-Cache"] = cache_result
        if entry is not None:
            response.headers["X-Trace-ID"] = entry["trace_id"]
        trace.record(cache=cache_result, response=result.answer,
                     citations=[citation.model_dump() for citation in result.citations],
                     answered_from_context=result.answered_from_context, status=200)
        return result
    except RuntimeError as exc:
        trace.record(status=503, outcome="unavailable", error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        trace.record(status=500, outcome="error", error=type(exc).__name__)
        raise
    finally:
        trace.stage("total", started)
        trace.current.reset(token)
        if entry is not None:
            try:
                trace.publish(entry, state.settings.trace_dir)
            except OSError:
                log.exception("could not persist query trace", extra={"trace_id": entry["trace_id"]})

"""POST /api/ask. The Ingress sends /api/* here unchanged, so the prefix stays."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ..schemas import AskRequest, AskResponse
from ..services.answer import answer_with_cache

router = APIRouter(prefix="/api")


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request, response: Response) -> AskResponse:
    state = request.app.state
    try:
        result, cache_result = answer_with_cache(body.question, state.retriever, state.provider,
                                                 state.settings, state.redis)
    except RuntimeError as exc:   # the index or the embedding service is unavailable
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # The response body is unchanged; a header says whether the cache answered.
    response.headers["X-Cache"] = cache_result
    return result

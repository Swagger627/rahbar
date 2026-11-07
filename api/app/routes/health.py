"""Liveness and readiness (section 4.3). They answer different questions."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Is the process alive? Must never touch Chroma, Redis or the LLM."""
    return {"status": "alive"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, object]:
    """Can this pod answer questions right now? Kubernetes only sends traffic to ready
    pods, so a 503 here keeps a pod with no usable index out of the Service."""
    settings = request.app.state.settings
    checks: dict[str, object] = {}
    try:
        if settings.retriever == "chroma":
            checks["index"] = request.app.state.retriever.check_index()
        else:
            checks["index"] = "stub retriever: no index needed"
        if settings.redis_url:
            checks["redis"] = check_redis(settings.redis_url)
    except RuntimeError as exc:
        response.status_code = 503
        return {"status": "not ready", "reason": str(exc), "checks": checks}
    return {"status": "ready", "checks": checks}


def check_redis(url: str) -> str:
    """Ping Redis. The error names the exception type only, never the URL, which may
    contain a password."""
    import redis

    try:
        redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2).ping()
    except Exception as exc:
        raise RuntimeError(f"redis unreachable: {type(exc).__name__}") from exc
    return "ok"

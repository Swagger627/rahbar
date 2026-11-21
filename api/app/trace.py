"""One query trace, shared by the route, retriever and provider."""

from __future__ import annotations

import json
import os
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings

current: ContextVar[dict[str, Any] | None] = ContextVar("query_trace", default=None)
_write_lock = threading.Lock()


def start(settings: Settings, trace_id: str, request_id: str, question: str) -> dict[str, Any]:
    return {
        "event": "rag_query", "trace_id": trace_id, "request_id": request_id,
        "timestamp": datetime.now(UTC).isoformat(), "query": question, "rewritten_query": None,
        "embedding_model": settings.embed_model, "embedding_version": settings.embed_model,
        "collection": settings.collection, "collection_version": None, "retrieved": [],
        "filters": [], "reranked_order": [], "context": [], "generation_attempts": [],
        "model": settings.llm_model, "model_parameters": {"temperature": settings.temperature,
                                                          "seed": settings.seed},
        "response": None, "citations": [], "guardrail_actions": [],
        "latency_ms": {}, "cache": "off",
    }


def record(**fields: Any) -> None:
    if (trace := current.get()) is not None:
        trace.update(fields)


def stage(name: str, started: float) -> None:
    if (trace := current.get()) is not None:
        latencies = trace["latency_ms"]
        latencies[name] = round(latencies.get(name, 0) + (time.perf_counter() - started) * 1000, 3)


def action(**fields: Any) -> None:
    if (trace := current.get()) is not None:
        trace["guardrail_actions"].append(fields)


def prompt(messages: list[dict[str, str]], parameters: dict[str, Any]) -> None:
    if (trace := current.get()) is not None:
        trace["generation_attempts"].append({"messages": messages, "parameters": parameters})


def publish(trace: dict[str, Any], directory: str) -> None:
    line = json.dumps(trace, ensure_ascii=False, separators=(",", ":"))
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    # One file per API pod, one critical section for all of its request threads.
    filename = f"{os.environ.get('HOSTNAME', 'local')}.jsonl"
    with _write_lock:
        print(line, flush=True)
        with (destination / filename).open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

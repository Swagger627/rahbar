"""Runtime configuration, read once from environment variables.

On the cluster these variables come from your ConfigMap and Secret (section 4.1).
Nothing here is baked into the image, so one image runs in every environment.
Add your own keys (relevance floor, cache settings, ...) the same way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # Which implementations to load. "stub" needs no cluster, no model, no network.
    retriever: str = "stub"          # RETRIEVER: stub | chroma
    llm_provider: str = "stub"       # LLM_PROVIDER: stub | ollama-host | ollama-incluster | hosted

    # The embedding pin (section 4.4). The indexer reads the same ConfigMap key.
    embed_model: str = "nomic-embed-text"

    # Vector store
    chroma_host: str = "chroma"
    chroma_port: int = 8000
    collection: str = "rahbar"

    # Retrieval and generation
    top_k: int = 5
    # RELEVANCE_FLOOR: passages scoring below this (1 - cosine distance) are not shown to
    # the model; if none is left, the API abstains without calling it. 0.65, measured with
    # nomic-embed-text on the 16 golden questions (docs/PIPELINE.md): every relevant top-5
    # passage scored >= 0.697, and the hostel-application question topped out at 0.585.
    # With 0.5 that question was once answered. A floor cannot catch every unanswerable
    # question (the hostel-fee one scores 0.736), so the model's own check still matters.
    relevance_floor: float = 0.65
    temperature: float = 0.2
    llm_model: str = "llama3.2:3b"
    ollama_url: str = "http://host.minikube.internal:11434"
    hosted_base_url: str = ""        # e.g. an OpenAI-compatible endpoint
    llm_api_key: str = field(default="", repr=False)   # from the Secret; never log it

    # Redis (the semantic cache, section 3.3). Empty means no cache and no Redis check in
    # /readyz (the stub setup for local development). It may hold a password: never log it.
    redis_url: str = field(default="", repr=False)
    # CACHE_THRESHOLD: reuse a stored answer only at cosine similarity >= this, and only if
    # the questions name the same numbers, grades and codes (services/cache.py). 0.90 was
    # measured on paraphrases and hard negatives (docs/PIPELINE.md): 0.80 let "hostel fee"
    # reuse a "tuition fee" answer; 0.85 had no false hit but only a 0.009 margin.
    cache_threshold: float = 0.90
    cache_ttl_seconds: int = 3600     # CACHE_TTL_SECONDS: a stored answer lives one hour

    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings(
        retriever=os.environ.get("RETRIEVER", "stub"),
        llm_provider=os.environ.get("LLM_PROVIDER", "stub"),
        embed_model=os.environ.get("EMBED_MODEL", "nomic-embed-text"),
        chroma_host=os.environ.get("CHROMA_HOST", "chroma"),
        chroma_port=_int("CHROMA_PORT", 8000),
        collection=os.environ.get("CHROMA_COLLECTION", "rahbar"),
        top_k=_int("TOP_K", 5),
        relevance_floor=_float("RELEVANCE_FLOOR", 0.65),
        temperature=_float("TEMPERATURE", 0.2),
        llm_model=os.environ.get("LLM_MODEL", "llama3.2:3b"),
        ollama_url=os.environ.get("OLLAMA_URL", "http://host.minikube.internal:11434"),
        hosted_base_url=os.environ.get("HOSTED_BASE_URL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        redis_url=os.environ.get("REDIS_URL", ""),
        cache_threshold=_float("CACHE_THRESHOLD", 0.90),
        cache_ttl_seconds=_int("CACHE_TTL_SECONDS", 3600),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )

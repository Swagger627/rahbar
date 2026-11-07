"""/healthz and /readyz through the real app, in stub mode and in chroma mode (with the
fake Chroma client from test_retrieval.py, so no server is needed)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.test_retrieval import ROWS, FakeClient, FakeCollection


@pytest.fixture()
def chroma_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("RETRIEVER", "chroma")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("EMBED_MODEL", "nomic-embed-text")
    monkeypatch.delenv("REDIS_URL", raising=False)

    def no_chroma(**kwargs: object) -> None:          # as if no Chroma server answers
        raise ConnectionError("connection refused")

    monkeypatch.setattr("chromadb.HttpClient", no_chroma)
    from app.main import app

    with TestClient(app) as client:
        yield client


def use(client: TestClient, fake: FakeClient) -> None:
    client.app.state.retriever._client = fake          # type: ignore[attr-defined]


def healthy() -> FakeClient:
    return FakeClient(FakeCollection(ROWS, {"embed_model": "nomic-embed-text", "dimension": 3}))


def test_stub_mode_is_ready_without_any_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVER", "stub")
    monkeypatch.delenv("REDIS_URL", raising=False)
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"index": "stub retriever: no index needed"}}


def test_chroma_down_at_startup_keeps_the_app_alive_but_not_ready(chroma_app: TestClient) -> None:
    assert chroma_app.get("/healthz").json() == {"status": "alive"}      # liveness unaffected
    r = chroma_app.get("/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "not ready"
    assert r.json()["reason"].startswith("chroma unreachable")


def test_healthy_index_is_ready(chroma_app: TestClient) -> None:
    use(chroma_app, healthy())
    r = chroma_app.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"index": {
        "collection": "rahbar", "rows": 4, "embed_model": "nomic-embed-text", "dimension": 3}}}


@pytest.mark.parametrize("fake, reason", [
    (FakeClient(None), "collection 'rahbar' not found"),
    (FakeClient(FakeCollection({}, {"embed_model": "nomic-embed-text", "dimension": 3})),
     "is empty"),
    (FakeClient(FakeCollection(ROWS, {"embed_model": "dummy-768", "dimension": 768})),
     "embedding model mismatch: the index was built with 'dummy-768'"),
    (FakeClient(FakeCollection(ROWS, {"embed_model": "nomic-embed-text", "dimension": 768})),
     "dimension 768, but a stored vector has 3"),
])
def test_each_failed_check_is_a_503_naming_it(chroma_app: TestClient, fake: FakeClient,
                                              reason: str) -> None:
    use(chroma_app, fake)
    r = chroma_app.get("/readyz")
    assert r.status_code == 503 and reason in r.json()["reason"]


def test_redis_is_checked_only_when_configured(chroma_app: TestClient,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    use(chroma_app, healthy())
    state = chroma_app.app.state                                      # type: ignore[attr-defined]
    secret_url = "redis://:s3cret-password@127.0.0.1:9/0"             # nothing listens on 9
    state.settings = replace(state.settings, redis_url=secret_url)
    r = chroma_app.get("/readyz")
    assert r.status_code == 503
    assert r.json()["reason"].startswith("redis unreachable")
    assert "s3cret" not in r.text                                     # never leak the URL


def test_ask_returns_503_when_the_index_is_unavailable(chroma_app: TestClient) -> None:
    r = chroma_app.post("/api/ask", json={"question": "What is the tuition fee?"})
    assert r.status_code == 503
    assert "chroma unreachable" in r.json()["detail"]

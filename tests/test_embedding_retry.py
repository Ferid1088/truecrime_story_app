import asyncio

import httpx
import pytest

from app.research_engine import embeddings as emb


class _Response:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=httpx.Request("POST", "http://x"), response=self)

    def json(self):
        return {"data": [{"embedding": [0.1, 0.2]}], "usage": {"total_tokens": 3}}


def _client(monkeypatch, statuses, tmp_path):
    calls = []

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            calls.append(1)
            return _Response(statuses[min(len(calls), len(statuses)) - 1])

    async def no_sleep(_):
        return None

    monkeypatch.setattr(emb.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(emb.asyncio, "sleep", no_sleep)
    client = emb.EmbeddingClient("http://x", "key", "m", cache_dir=str(tmp_path))
    return client, calls


def test_a_transient_gateway_5xx_is_retried_then_succeeds(monkeypatch, tmp_path):
    """A live research run lost its whole job to one transient HTTP 500."""
    client, calls = _client(monkeypatch, [500, 502, 200], tmp_path)
    vectors = asyncio.run(client.embed(["some text"]))
    assert vectors.shape == (1, 2)
    assert len(calls) == 3


def test_a_persistent_5xx_fails_after_the_configured_attempts(monkeypatch, tmp_path):
    client, calls = _client(monkeypatch, [500], tmp_path)
    with pytest.raises(emb.EmbeddingError, match="HTTP 500"):
        asyncio.run(client.embed(["some text"]))
    assert len(calls) == emb.ai_config.embeddings.max_attempts


def test_a_client_error_is_not_retried(monkeypatch, tmp_path):
    client, calls = _client(monkeypatch, [400], tmp_path)
    with pytest.raises(emb.EmbeddingError, match="HTTP 400"):
        asyncio.run(client.embed(["some text"]))
    assert len(calls) == 1

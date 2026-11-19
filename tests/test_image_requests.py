"""Image generation through the gateway without any network: at most the configured number of requests run together and the rest queue, a 429 or an overloaded gateway pulls
the limit down and its Retry-After is honoured between attempts (capped), a request that was sent and never answered is marked as possibly billed, and every attempt is still recorded."""

from __future__ import annotations

import asyncio
import calendar

import httpx
import pytest
from test_cover_images import FakeImages, bad, cover, good  # noqa: F401  (the `cover` fixture)

from app.core import concurrency
from app.covers import images as IM
from app.providers.image import apimaster as AM
from app.providers.image.apimaster import APIMasterImageProvider, ImageResult


@pytest.fixture(autouse=True)
def _keep_the_call_ledger_clean(db_session):
    """The mocked answers of these tests (429, 503, timeouts) would otherwise stay in the shared outgoing-call ledger and make a later monitoring test see a failing provider."""
    from sqlalchemy import func

    from app.core import observability
    from app.db.models import ProviderCall

    observability.flush()
    before = db_session.query(func.coalesce(func.max(ProviderCall.id), 0)).scalar()
    yield
    observability.flush()                  # the ledger is written by a background thread: wait for it, then remove what these tests caused
    db_session.rollback()
    db_session.query(ProviderCall).filter(ProviderCall.id > before).delete()
    db_session.commit()


@pytest.fixture
def gateway(monkeypatch):
    """The provider with its HTTP replaced by a local handler; `answers` is called for every request."""
    monkeypatch.delenv("TRUECRIME_TEST_NO_REAL_PROVIDERS", raising=False)          # only to construct the object
    provider = APIMasterImageProvider()
    provider.api_key = "test-key-not-used"
    state = {"now": 0, "max": 0, "handler": None}
    real = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        return await state["handler"](request)

    monkeypatch.setattr(AM.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    concurrency.reset_throttles()
    yield provider, state
    concurrency.reset_throttles()


def ok_json() -> httpx.Response:
    return httpx.Response(200, json={"data": [{"url": "https://gateway.example/picture.png"}], "usage": {"output_tokens": 10}, "created": 1})


def test_at_most_the_configured_number_of_image_requests_run_together_and_the_rest_queue(gateway):
    provider, state = gateway

    async def handler(request):
        if request.url.path.endswith("picture.png"):
            return httpx.Response(200, content=b"png")
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return ok_json()

    state["handler"] = handler

    async def go():
        return await asyncio.gather(*(provider.generate("m", "p", "1024x1024", 5) for _ in range(8)))

    results = asyncio.run(go())
    assert all(r.ok for r in results), "nothing failed because of the queue"
    assert state["max"] == concurrency.limit_size("image") == 2


def test_a_too_many_requests_answer_pulls_the_limit_down_and_carries_the_retry_after(gateway):
    provider, state = gateway

    async def handler(request):
        return httpx.Response(429, json={"error": "slow down"}, headers={"Retry-After": "7"})

    state["handler"] = handler
    result = asyncio.run(provider.generate("m", "p", "1024x1024", 5))
    assert not result.ok and result.rate_limited and result.retry_after_s == 7.0 and not result.uncertain
    assert concurrency.throttled("image") and concurrency.effective_limit("image") == 1


def test_an_overloaded_gateway_is_treated_like_a_rate_limit(gateway):
    provider, state = gateway

    async def handler(request):
        return httpx.Response(503, json={"error": {"message": "CPU overloaded"}})

    state["handler"] = handler
    result = asyncio.run(provider.generate("m", "p", "1024x1024", 5))
    assert result.rate_limited and result.retry_after_s is None and concurrency.throttled("image")


def test_a_request_that_was_sent_and_not_answered_is_marked_as_possibly_billed(gateway):
    provider, state = gateway

    async def handler(request):
        raise httpx.ReadTimeout("no answer")

    state["handler"] = handler
    result = asyncio.run(provider.generate("m", "p", "1024x1024", 5))
    assert not result.ok and result.uncertain and "may have been billed" in result.error and not result.rate_limited


def test_a_refused_connection_is_not_marked_as_possibly_billed(gateway):
    provider, state = gateway

    async def handler(request):
        raise httpx.ConnectError("never connected")

    state["handler"] = handler
    result = asyncio.run(provider.generate("m", "p", "1024x1024", 5))
    assert not result.ok and not result.uncertain


def test_retry_after_is_read_as_seconds_or_as_a_date_and_a_wrong_value_is_ignored():
    assert concurrency.retry_after_seconds("12") == 12.0
    assert concurrency.retry_after_seconds("Wed, 21 Oct 2026 07:28:30 GMT", now=calendar.timegm((2026, 10, 21, 7, 28, 0))) == 30.0
    assert concurrency.retry_after_seconds("Wed, 21 Oct 2026 07:28:30 GMT", now=4_000_000_000) == 0.0
    assert concurrency.retry_after_seconds("soon") is None and concurrency.retry_after_seconds(None) is None and concurrency.retry_after_seconds("") is None


def test_the_next_attempt_waits_for_the_retry_after_and_never_longer_than_the_configured_cap(db_session, cover, monkeypatch):  # noqa: F811
    case, row = cover
    slept: list[float] = []

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(IM.asyncio, "sleep", record)
    limited = ImageResult(ok=False, error="429: busy", rate_limited=True, retry_after_s=9.0)
    far = ImageResult(ok=False, error="429: busy", rate_limited=True, retry_after_s=100000.0)
    asyncio.run(IM.make_image(db_session, case, "en", row, FakeImages([limited, far, good()])))
    assert slept == [9.0, 60.0], "the gateway's pause is given, but a wrong or huge value cannot stall the work for longer than the cap"


def test_an_attempt_that_got_no_answer_is_recorded_and_logged_before_the_next_one(db_session, cover, caplog):  # noqa: F811
    case, row = cover
    silent = ImageResult(ok=False, error="ReadTimeout: x (the request was sent and not answered: it may have been billed)", uncertain=True)
    with caplog.at_level("WARNING"):
        asyncio.run(IM.make_image(db_session, case, "en", row, FakeImages([silent, good()])))
    from app.db.models import CoverImage

    attempts = db_session.query(CoverImage).filter(CoverImage.case_id == case.id).order_by(CoverImage.id).all()
    assert "may have been billed" in attempts[0].error and attempts[1].ok
    assert any("may have been billed" in r.message for r in caplog.records)


def test_the_image_limit_is_configured_and_shows_in_the_monitor_data():
    assert concurrency.limit_size("image") == 2
    from app.core.ai_config import ai_config

    assert "image" in ai_config.concurrency.model_dump()

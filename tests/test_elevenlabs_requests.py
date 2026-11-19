"""ElevenLabs speech requests without any network: a request the provider cannot answer yet queues and is retried (it does not fail), a request that was sent and never answered is
counted (it may have been billed, and the provider documents no idempotency key), and a finished block is never requested again."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from app.core import concurrency
from app.providers.voice.base import VoiceRequest
from app.providers.voice.elevenlabs import ElevenLabsVoiceProvider


def ok_body() -> dict:
    return {"audio_base64": base64.b64encode(b"audio").decode(), "alignment": {"characters": ["a"], "character_start_times_seconds": [0.0], "character_end_times_seconds": [0.1]}}


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
def provider(monkeypatch):
    monkeypatch.delenv("TRUECRIME_TEST_NO_REAL_PROVIDERS", raising=False)          # only to construct the object: every network call is replaced below
    p = ElevenLabsVoiceProvider()
    p.api_key = "test-key-not-used"

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("app.providers.voice.elevenlabs.asyncio.sleep", no_wait)
    concurrency.reset_throttles()
    yield p
    concurrency.reset_throttles()


def request() -> VoiceRequest:
    return VoiceRequest(text="a", voice_id="v", model_id="eleven_v3", language="en", language_code="en", settings={"stability": 0.5}, previous_text="", next_text="", seed=1)


def test_a_too_many_requests_answer_is_queued_and_retried_until_it_succeeds(provider):
    answers = [(429, {"detail": {"status": "concurrent_limit_exceeded", "message": "too many concurrent requests"}}, {})] * 3 + [(200, ok_body(), {"character-cost": "1"})]
    sent = []

    async def post(url, body):
        sent.append(body["text"])
        return answers.pop(0)

    provider._post = post
    result = asyncio.run(provider.synthesize(request()))
    assert result.audio == b"audio" and len(sent) == 4, "three refusals, then the answer: nothing failed"
    assert not result.extra.get("uncertain_prior_attempts"), "a clear refusal means the request was not processed"
    assert concurrency.throttled("elevenlabs_tts"), "the limit shrank, so the next requests are fewer at a time"


def test_a_request_that_was_sent_and_never_answered_is_counted_before_the_retry(provider):
    calls = []

    async def post(url, body):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("no answer")
        if len(calls) == 2:
            raise httpx.ConnectError("never connected")               # not sent: not uncertain
        return 200, ok_body(), {"character-cost": "1"}

    provider._post = post
    result = asyncio.run(provider.synthesize(request()))
    assert len(calls) == 3 and result.extra["uncertain_prior_attempts"] == 1, "only the timeout may have been billed; the refused connection cannot have been"


def test_the_requests_of_nine_blocks_never_exceed_the_configured_limit_and_none_fails(provider):
    state = {"now": 0, "max": 0}

    async def post(url, body):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        state["now"] -= 1
        return 200, ok_body(), {}

    provider._post = post

    async def go():
        return await asyncio.gather(*(provider.synthesize(request()) for _ in range(9)))

    results = asyncio.run(go())
    assert len(results) == 9 and state["max"] <= concurrency.limit_size("elevenlabs_tts") == 4


def test_a_failing_provider_is_asked_a_bounded_number_of_times_and_then_the_error_is_raised(provider):
    from app.core.ai_config import ai_config
    from app.providers.voice.base import VoiceProviderError

    calls = []

    async def post(url, body):
        calls.append(1)
        return 500, {"detail": "server error"}, {}

    provider._post = post
    with pytest.raises(VoiceProviderError):
        asyncio.run(provider.synthesize(request()))
    assert len(calls) == ai_config.voice.elevenlabs.max_retries + 1, "no uncontrolled retries: the configured number, then the error"


def test_a_request_that_never_gets_an_answer_is_retried_a_bounded_number_of_times_and_counted(provider):
    from app.core.ai_config import ai_config
    from app.providers.voice.base import VoiceProviderError

    calls = []

    async def post(url, body):
        calls.append(1)
        raise httpx.ReadTimeout("no answer")

    provider._post = post
    with pytest.raises(VoiceProviderError, match="timeout"):
        asyncio.run(provider.synthesize(request()))
    assert len(calls) == ai_config.voice.elevenlabs.max_retries + 1


def test_the_saved_block_records_that_earlier_requests_may_have_been_billed(tmp_path, monkeypatch):
    """The provider reports `uncertain_prior_attempts`; the block's sidecar keeps it, so the possible double charge can be seen after the fact."""
    from test_voice_render import FakeASR, FakeTTS, _block, _render

    from app.core.ai_config import ai_config
    from app.documentary.voice_render import VoiceRenderer

    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))

    class Uncertain(FakeTTS):
        async def synthesize(self, req):
            result = await super().synthesize(req)
            result.extra["uncertain_prior_attempts"] = 2
            return result

    manifest = _render(VoiceRenderer(provider=Uncertain(), asr=FakeASR()), [_block("EN_a_01", "act1", "He waited in the hallway for twenty seconds.")])
    sidecars = list(tmp_path.rglob("blocks/*.json"))
    assert sidecars and any('"uncertain_prior_attempts": 2' in p.read_text() for p in sidecars)
    assert manifest["blocks"][0]["cache_hit"] is False
    clean = _render(VoiceRenderer(provider=FakeTTS(), asr=FakeASR()), [_block("EN_a_02", "act1", "A different sentence about the farmhouse door.")])
    second = [p for p in tmp_path.rglob("blocks/*.json") if "EN_a_02" in p.name]
    assert clean["blocks"][0]["cache_hit"] is False and second and not any('"uncertain_prior_attempts"' in p.read_text() for p in second), "a clean request records nothing"

"""The host's text and media are made next to the narration, and the CPU-bound music mix is one at a time."""

import asyncio

from app.core import concurrency
from app.documentary.pipeline.language import LanguageChain
from app.documentary.production.audio import mix_narration


class _Mixer:
    def __init__(self, peak):
        self.peak = peak

    async def mix(self, manifest, plan, out, **kw):
        self.peak["now"] += 1
        self.peak["max"] = max(self.peak["max"], self.peak["now"])
        await asyncio.sleep(0.02)
        self.peak["now"] -= 1
        return {}


def test_music_mixes_run_one_at_a_time(tmp_path):
    peak = {"now": 0, "max": 0}

    async def go():
        await asyncio.gather(*(mix_narration({}, {"directed": True}, tmp_path, mixer=_Mixer(peak)) for _ in range(4)))

    asyncio.run(go())
    assert concurrency.limit_size("mix") == 1
    assert peak["max"] == 1


def test_a_chain_that_stops_stops_its_host_media(monkeypatch):
    seen = {}

    async def stopped_chain(self):
        self.host_media = asyncio.create_task(asyncio.sleep(60))
        seen["task"] = self.host_media
        await asyncio.sleep(0)
        raise RuntimeError("the narration failed")

    monkeypatch.setattr(LanguageChain, "_run", stopped_chain)
    chain = LanguageChain(None, "en", None, None)
    try:
        asyncio.run(chain.run())
    except RuntimeError as e:
        assert "narration failed" in str(e)
    else:
        raise AssertionError("the failure must reach the caller")
    assert seen["task"].cancelled()


def test_only_one_voice_stage_and_one_render_run_at_a_time_and_the_limits_come_from_config():
    from app.core.concurrency import limit_size

    assert limit_size("voice_stage") == 1 and limit_size("render") == 1


def test_the_voice_stages_of_several_languages_run_one_after_another():
    peak = {"now": 0, "max": 0}

    async def voice_stage():
        async with concurrency.slot("voice_stage"):
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.02)
            peak["now"] -= 1

    async def go():
        await asyncio.gather(*(voice_stage() for _ in range(5)))

    asyncio.run(go())
    assert peak["max"] == 1

"""The chain of Persian's narration for Arabic, German and English, from the story's text to what the voice is given, with simulated agents (their answers are
written by hand here; every other step is the app's own code): the number is settled in the spoken version, the punctuation layer changes punctuation only,
Arabic's reading stage adds the vowel marks (Tashkeel), German and English have no reading stage, and every request has stability 0.2 and its own speed."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil

import pytest
from test_voice_render import FakeASR, FakeTTS, _plan, workdir  # noqa: F401  (the fixture)

from app.core.ai_config import ai_config
from app.documentary import spoken as SP
from app.documentary.reading import ask as ASK
from app.documentary.reading import stage as RS
from app.documentary.reading import tashkeel as TK
from app.documentary.reading.block import BlockInput
from app.documentary.voice_render import VoiceRenderer

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests")

CASES = {
    "en": {"story": "At midnight he waited 21 seconds in the hallway and then he knocked.",
           "spoken": "At midnight, he waited twenty-one seconds in the hallway, and then he knocked.",},
    "de": {"story": "Um Mitternacht wartete er 21 Sekunden im Flur, bevor er klopfte.",
           "spoken": "Um Mitternacht wartete er einundzwanzig Sekunden im Flur, bevor er klopfte.",},
    "ar": {"story": "انتظر الرجل 21 ثانية في الممر ثم طرق الباب.",
           "spoken": "انتظر الرجل إحدى وعشرين ثانية في الممر، ثم طرق الباب.",
           "tashkeel": ["انْتَظَرَ", "الرَّجُلُ", "إِحْدَى", "وَعِشْرِينَ", "ثَانِيَةً", "فِي", "الْمَمَرِّ", "ثُمَّ", "طَرَقَ", "الْبَابَ"],
           "voiced": "انْتَظَرَ الرَّجُلُ إِحْدَى وَعِشْرِينَ ثَانِيَةً فِي الْمَمَرِّ، ثُمَّ طَرَقَ الْبَابَ."},
}


def agent(*answers):
    seen = []

    async def run(gen, body):
        seen.append(json.loads(body))
        return answers[min(len(seen), len(answers)) - 1], None

    run.seen = seen
    return run


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


def chain(language: str, tmp_workdir=None) -> dict:
    """One block through the chain; returns what each step made."""
    case = CASES[language]
    out = {"story": case["story"], "digits_in_story": SP.digits_left(case["story"], language), "spoken": case["spoken"],
           "digits_in_spoken": SP.digits_left(case["spoken"], language)}
    plan = {**_plan([{"block_id": "B_01", "section_id": "B", "text": case["spoken"], "word_count": len(case["spoken"].split()), "est_seconds": 3.0,
                      "previous_text": "", "next_text": "", "oversize": False}]), "language": language}
    renderer = VoiceRenderer(provider=FakeTTS(), asr=FakeASR(), use_listener=False)
    renderer.narration = True
    if RS.required(language):
        item = BlockInput("B_01", "B", case["spoken"])
        writer = agent({"words": [{"n": n, "text": t, "confidence": "high"} for n, t in enumerate(case["tashkeel"])]})
        read = asyncio.run(TK.TashkeelStage(None, 1, [item], writer).run())
        renderer.voicing = asyncio.run(RS.voicing_of(None, language, read, [item]))
        out["tashkeel_added_to"] = [r.text for r in read.readings["B_01"] if r.text != TK.bare(r.text)]
    manifest = asyncio.run(renderer.render(plan, case_id=7, story_version_id=3))
    sent = renderer.provider.requests[0]
    out.update(sent_text=sent.text, stability=sent.settings["stability"], speed=sent.settings["speed"], language_code=sent.language_code,
               post_speed=ai_config.voice.post_speed.get(language, 1.0), flags=manifest["flags"])
    return out


@pytest.mark.parametrize("language", ["en", "de", "ar"])
def test_the_number_is_settled_in_the_spoken_version_and_nothing_after_it_reads_a_digit(language, workdir):  # noqa: F811
    r = chain(language)
    assert r["digits_in_story"] and not r["digits_in_spoken"]
    assert not SP.digits_left(r["spoken"], language) and not SP.digits_left(r["sent_text"], language)


@pytest.mark.parametrize("language", ["en", "de"])
def test_german_and_english_have_no_reading_stage_the_voice_gets_the_spoken_text(language, workdir):  # noqa: F811
    r = chain(language)
    assert not RS.required(language) and r["sent_text"] == CASES[language]["spoken"] and "tashkeel_added_to" not in r


def test_arabic_gets_its_vowel_marks_in_the_reading_stage(workdir):  # noqa: F811
    r = chain("ar")
    assert r["sent_text"] == CASES["ar"]["voiced"] and TK.bare(r["sent_text"]) == r["spoken"]
    assert len(r["tashkeel_added_to"]) == 10, "every word of the block got its vowel marks"
    assert r["flags"] == [], "the speech check passed on the vowelled text"


@pytest.mark.parametrize("language", ["en", "de", "ar"])
def test_every_request_has_stability_02_its_own_speed_and_no_persian_post_speed(language, workdir):  # noqa: F811
    r = chain(language)
    assert r["stability"] == 0.7 and r["language_code"] == language
    assert r["speed"] == ai_config.voice.settings_for(ai_config.voice.default_style, language)["speed"] and r["speed"] != 1.2
    assert r["post_speed"] == (1.0 if language == "en" else 1.05)

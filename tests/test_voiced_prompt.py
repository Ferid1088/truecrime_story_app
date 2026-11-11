"""The words the avatar speaks (the opening block, the short clips) are written with the punctuation rules of the language's voice director, the part the host's writer
gets too: no new agent sets their commas afterwards."""

import asyncio
import json

from test_host import _plan_story, _version
from test_short_clips import ClipGen

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.documentary import host as H
from app.shortform import clip_agents as CA


def test_the_prompt_for_spoken_words_carries_the_punctuation_rules_and_the_notes_of_the_language():
    for name in ("shortform/clip_native", "shortform/clip_director", "documentary/opening/native_system", "documentary/opening/director_system"):
        text = H.voiced_prompt(name, "fa")
        assert text.startswith(prompt(name)) and "PUNCTUATION FOR THE VOICE" in text
        assert ai_config.voice_performance.punctuation_notes["fa"] in text
        assert "your words are voiced as they are written, so set the punctuation too" in text      # the host's wording: no agent edits the marks afterwards


def test_a_language_without_punctuation_rules_gets_the_prompt_as_it_is(monkeypatch):
    monkeypatch.setattr(ai_config.voice_performance, "punctuation_languages", ["en"])
    assert H.voiced_prompt("shortform/clip_native", "fa") == prompt("shortform/clip_native")


def test_the_clip_writers_send_the_rules_to_the_model(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    seen = []

    class Gen(ClipGen):
        async def generate_structured(self, role, system, user, images=None):
            seen.append((role, json.loads(user).get("language"), system))
            return await super().generate_structured(role, system, user, images)

    gen = Gen()
    monkeypatch.setattr("app.shortform.clip_agents.get_generation_provider", lambda: gen)
    planner = CA.ClipPlanner()
    english = asyncio.run(planner.plan_english(db_session, case, master))
    fa = _version(db_session, case, master, "en")
    fa.language = "fa"
    db_session.commit()
    asyncio.run(planner.write_native(db_session, case, fa, english))
    roles = {role: system for role, _lang, system in seen}
    assert "PUNCTUATION FOR THE VOICE" in roles["short_clip_director"] and "PUNCTUATION FOR THE VOICE" in roles["short_clip_native_writer"]
    assert ai_config.voice_performance.punctuation_notes["fa"] in roles["short_clip_native_writer"]

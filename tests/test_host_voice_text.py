"""The host's words are written with the storyteller's craft and prepared for the voice like the narration's blocks: the punctuation layer in every language,
the reading text in Persian and Arabic (Tashkeel), none in German and English. The host keeps its persona, its words and the text the avatar shows; only
what the voice is given changes. The agents are fakes."""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from test_studio import FakeVoice, _renderer, _segments

from app.core.ai_config import ai_config
from app.documentary import host as H
from app.documentary import host_voice_text as HVT
from app.documentary.reading import ask as ASK
from app.documentary.reading import tashkeel as TK

LANGUAGES = ["fa", "ar", "en", "de", "es", "it", "fr", "pt", "tr"]


@pytest.fixture(autouse=True)
def prepared(monkeypatch):
    monkeypatch.setattr(ai_config.host, "text_preparation", list(LANGUAGES))
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


def agent(*answers):
    seen = []

    async def run(gen, body):
        seen.append(json.loads(body))
        return answers[min(len(seen), len(answers)) - 1], None

    run.seen = seen
    return run


class Renderer:
    voicing = planner = "stale"


class Scene:
    id, case_id, story_version_id = 7, 1, 1

    def __init__(self, language: str, text: str):
        self.language, self.text = language, text


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_writer_gets_the_craft_and_keeps_its_persona(language):
    system = H.writer_system_prompt(language)
    assert "STORYTELLER'S CRAFT" in system and H.persona_prompt(ai_config.host) in system
    assert f"at most {ai_config.spoken.max_sentence_words[language]} words" in system
    assert ("NUMBERS (this is where they are settled)" in system) == (language in ai_config.spoken.numbers_as_words)


def test_the_craft_of_each_language_is_its_own():
    assert "contractions" in H.craft_of("en") and "Schachtelsätze" in H.craft_of("de")
    assert "ezafe" in H.craft_of("fa") and "idafa" in H.craft_of("ar")
    own = {"es": "Mexico", "it": "passato prossimo", "fr": "passé composé", "pt": "Brazil", "tr": "verb last"}
    assert all(word in H.craft_of(language) for language, word in own.items())


def test_the_turkish_craft_teaches_by_bad_and_good_examples_like_the_persian_one():
    craft = H.craft_of("tr")
    assert craft.count("Bad (") >= 4 and craft.count("Good:") >= 4
    assert "-miş" in craft and "Beni asıl iki kızı durdurdu" in craft


def test_every_channel_language_has_a_craft_so_the_host_writer_can_start():
    for language in ai_config.channels:
        assert H.writer_system_prompt(language), language


@pytest.mark.parametrize("language,text", [("en", "I waited 21 seconds."), ("de", "Ich wartete 21 Sekunden."), ("ar", "انتظرت 21 ثانية.")])
def test_a_host_text_with_a_digit_goes_back_to_the_writer_where_numbers_are_written_out(language, text):
    seg = {"target_seconds": 5, "position": "mid", "role": "transition"}
    issues = H.dialogue_issues(seg, text, language, [], [])
    assert any(i["type"] == "numbers_as_digits" and "21" in i["quote"] for i in issues)


def test_a_persian_host_text_keeps_its_numbers_for_the_readings_code():
    seg = {"target_seconds": 5, "position": "mid", "role": "transition"}
    assert not any(i["type"] == "numbers_as_digits" for i in H.dialogue_issues(seg, "تو ۲۱ ثانیه صبر کردی؟", "fa", [], []))


@pytest.mark.parametrize("language,text", [
    ("en", "Last night, I waited twenty-one seconds in the hallway and then I knocked"),
    ("de", "Ich wartete einundzwanzig Sekunden im Flur, bevor ich klopfte"),
])
def test_german_and_english_keep_the_writers_punctuation_and_get_no_reading(language, text):
    renderer, scene = Renderer(), Scene(language, text)
    assert asyncio.run(HVT.prepare(None, scene, renderer)) == text, "the host writer sets the marks: no punctuation layer runs"
    assert renderer.voicing is None and renderer.planner is None, "no reading stage for this language"
    assert scene.text == text, "what the avatar shows never changes"


def test_arabic_gets_its_vowel_marks_on_the_writers_text():
    text = "انتظرت ثانية في الممر ثم طرقت الباب"
    marks = ["انْتَظَرْتُ", "ثَانِيَةً", "فِي", "الْمَمَرِّ", "ثُمَّ", "طَرَقْتُ", "الْبَابَ"]
    renderer, scene = Renderer(), Scene("ar", text)

    async def reader(db, case, version, language, items):
        assert [i.text for i in items] == [text], "the reading is made on the writer's text: no punctuation layer runs"
        return await TK.TashkeelStage(None, 1, items, agent({"words": [{"n": n, "text": t} for n, t in enumerate(marks)]})).run()

    class Db:
        def get(self, model, key):
            return None

    prepared = asyncio.run(HVT.prepare(Db(), scene, renderer, reader=reader))
    assert prepared == text and scene.text == text
    assert isinstance(renderer.voicing, TK.TashkeelVoicing) and renderer.planner is None
    assert renderer.voicing.text(HVT.block_id(scene), prepared) == "انْتَظَرْتُ ثَانِيَةً فِي الْمَمَرِّ ثُمَّ طَرَقْتُ الْبَابَ"


def test_persian_gets_the_ppie_reading_and_the_risk_planner_without_a_punctuation_layer(monkeypatch):
    from app.documentary.reading import stage as RS

    text = "یه شب تو خونه صبر کردم و بعد در زدم"
    asked = {}

    async def reader(db, case, version, language, items):
        asked["items"] = items
        return "READ"

    async def voicing_of(db, language, read, items):
        asked.update(read=read)
        return "VOICING"

    class Db:
        def get(self, model, key):
            return None

    monkeypatch.setattr(RS, "voicing_of", voicing_of)
    monkeypatch.setattr(ai_config.pronunciation_risk, "enabled", True)
    renderer, scene = Renderer(), Scene("fa", text)
    prepared = asyncio.run(HVT.prepare(Db(), scene, renderer, reader=reader))
    assert prepared == text and scene.text == text
    assert [i.text for i in asked["items"]] == [prepared] and asked["read"] == "READ"
    assert renderer.voicing == "VOICING" and renderer.planner is not None and renderer.planner.language == "fa"


def test_a_language_that_is_not_prepared_gets_its_text_as_it_is_and_no_stale_reading(monkeypatch):
    monkeypatch.setattr(ai_config.host, "text_preparation", ["fa"])
    renderer = Renderer()
    assert asyncio.run(HVT.prepare(None, Scene("en", "Just this."), renderer)) is None
    assert renderer.voicing is None and renderer.planner is None


def test_the_voice_step_speaks_the_prepared_text_while_the_scene_keeps_its_own(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes, run_scene

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    scene = plan_host_scenes(db_session, _segments(db_session, "de"))[0]
    original = scene.text
    spoken = []

    class Voice(FakeVoice):
        async def synthesize(self, req):
            spoken.append(req.text)
            return await super().synthesize(req)

    async def prepare(db, sc, renderer, punctuation=None, reader=None):
        return "Das ist, was die Stimme bekommt."

    monkeypatch.setattr(HVT, "prepare", prepare)
    asyncio.run(run_scene(db_session, scene, "voice", renderer=_renderer(Voice())))
    assert spoken == ["Das ist, was die Stimme bekommt."] and scene.text == original
    assert json.loads(scene.voice_checks_json)["tts_text"] == "Das ist, was die Stimme bekommt."


def test_the_german_voice_is_given_the_hosts_name_spelled_for_it_and_the_avatar_shows_it_unchanged():
    text = "Ich bin Fereidoun, und hier erzähle ich dir wahre Kriminalfälle."
    renderer, scene = Renderer(), Scene("de", text)
    spoken = asyncio.run(HVT.prepare(None, scene, renderer))
    assert spoken == "Ich bin Fereydun, und hier erzähle ich dir wahre Kriminalfälle."
    assert scene.text == text, "what the avatar shows never changes"
    assert HVT.respelled("Fereidouns Fall", "de") == "Fereidouns Fall", "only the whole word is respelled"
    assert HVT.respelled("I'm Fereidoun, and here I tell you.", "en") == "I'm Fereydun, and here I tell you."
    assert HVT.respelled("من فریدونم", "fa") == "من فریدونم", "Persian keeps its own"

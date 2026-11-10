"""The host's words are written with the storyteller's craft (the writer sets the punctuation) and the words a voice reads wrongly are spelled for it. The host
keeps its persona, its words and the text the avatar shows; only what the voice is given changes. The agents are fakes."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_studio import FakeVoice, _renderer, _segments

from app.core.ai_config import ai_config
from app.documentary import host as H
from app.documentary import host_voice_text as HVT

LANGUAGES = ["fa", "ar", "en", "de", "es", "it", "fr", "pt", "tr"]


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


def test_a_persian_host_text_keeps_its_numbers_for_the_number_layer():
    seg = {"target_seconds": 5, "position": "mid", "role": "transition"}
    assert not any(i["type"] == "numbers_as_digits" for i in H.dialogue_issues(seg, "تو ۲۱ ثانیه صبر کردی؟", "fa", [], []))


@pytest.mark.parametrize("language,text", [
    ("en", "Last night, I waited twenty-one seconds in the hallway and then I knocked"),
    ("de", "Ich wartete einundzwanzig Sekunden im Flur, bevor ich klopfte"),
    ("ar", "انتظرت ثانية في الممر ثم طرقت الباب"),
    ("fa", "یه شب تو خونه صبر کردم و بعد در زدم"),
])
def test_a_text_with_no_respelled_word_goes_to_the_voice_as_the_writer_wrote_it(language, text):
    scene = Scene(language, text)
    assert HVT.prepare(scene) is None, "the host writer sets the marks: nothing else touches the text"
    assert scene.text == text, "what the avatar shows never changes"


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

    def prepare(sc):
        return "Das ist, was die Stimme bekommt."

    monkeypatch.setattr(HVT, "prepare", prepare)
    asyncio.run(run_scene(db_session, scene, "voice", renderer=_renderer(Voice())))
    assert spoken == ["Das ist, was die Stimme bekommt."] and scene.text == original
    assert json.loads(scene.voice_checks_json)["tts_text"] == "Das ist, was die Stimme bekommt."


def test_the_german_voice_is_given_the_hosts_name_spelled_for_it_and_the_avatar_shows_it_unchanged():
    text = "Ich bin Fereidoun, und hier erzähle ich dir wahre Kriminalfälle."
    scene = Scene("de", text)
    spoken = HVT.prepare(scene)
    assert spoken == "Ich bin Fereydun, und hier erzähle ich dir wahre Kriminalfälle."
    assert scene.text == text, "what the avatar shows never changes"
    assert HVT.respelled("Fereidouns Fall", "de") == "Fereidouns Fall", "only the whole word is respelled"
    assert HVT.respelled("I'm Fereidoun, and here I tell you.", "en") == "I'm Fereydun, and here I tell you."
    assert HVT.respelled("من فریدونم", "fa") == "من فریدونم", "Persian keeps its own"


def test_the_host_writer_is_given_the_punctuation_rules_of_the_voice_for_its_language():
    german = H.writer_system_prompt("de")
    persian = H.writer_system_prompt("fa")

    assert "PUNCTUATION FOR THE VOICE" in german and "mandatory grammar" in german
    assert "ezafe" in persian and "FORBIDDEN" in persian
    assert "your words are voiced as they are written" in german
    assert H.punctuation_for_the_voice("zz") == "", "a language the director does not punctuate adds nothing"

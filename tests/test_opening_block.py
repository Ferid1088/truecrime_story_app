"""The film's opening block: the checks of a hook and a context text, the voice of the block per language, and the two agents (English director,
native writer) with a scripted model."""

import asyncio
import json

import pytest
from test_host import _plan_story, _version, story

from app.agents.opening import OpeningWriter, quotes_of
from app.core.ai_config import ai_config
from app.db.models import FilmOpening
from app.documentary import opening_block as OB
from app.providers.generation.base import GenerationResult

GOOD_HOOK = "Someone locked the door from the inside, and nobody was there."
GOOD_CONTEXT = ("A lantern in the hall. A door that should have been open. Nobody explains it. "
                "That night two people tell very different stories, and only one of them can be true.")


def codes(issues):
    return {i["type"] for i in issues}


def test_a_good_hook_and_context_have_no_issue():
    assert OB.opening_issues({"hook": GOOD_HOOK, "context": GOOD_CONTEXT}, story(), "en") == []


def test_a_greeting_digits_and_a_later_detail_are_refused():
    issues = OB.opening_issues({"hook": "Hello and welcome back to the channel.", "context": GOOD_CONTEXT + " Wexford changed the lock in 1998."}, story(), "en")
    assert {"greeting", "numbers_as_digits", "tells_what_comes_later"} <= codes(issues)


def test_the_hook_is_one_statement_without_a_narrators_tag():
    tagged = '"Someone broke into my house and shot my wife." The man on the phone said so.'
    assert "hook_not_one_statement" in codes(OB.opening_issues({"hook": tagged, "context": GOOD_CONTEXT}, story(), "en"))
    assert "hook_not_one_statement" not in codes(OB.opening_issues({"hook": GOOD_HOOK, "context": GOOD_CONTEXT}, story(), "en"))


def test_the_limits_follow_the_language():
    long_hook = " ".join(["word"] * 40)
    assert "hook_too_long" in codes(OB.opening_issues({"hook": long_hook, "context": GOOD_CONTEXT}, story(), "en"))
    assert OB.hook_max_words("tr") < OB.hook_max_words("en")           # Turkish is slower: fewer words fill the same seconds
    lo, hi = OB.context_limits("en")
    assert "context_too_short" in codes(OB.opening_issues({"hook": GOOD_HOOK, "context": "Short."}, story(), "en")) and lo < hi


def test_the_context_may_not_repeat_the_narration_but_the_hook_may_be_its_quote():
    told = " ".join(story().acts[0].text.split()[:12])
    issues = OB.opening_issues({"hook": told, "context": GOOD_CONTEXT + " " + told}, story(), "en")
    assert [i["part"] for i in issues if i["type"] == "repeats_the_narration"] == ["context"]


def test_a_context_of_only_long_sentences_is_refused():
    long_sentences = "Two people in one house tell very different stories about that night. " * 3
    assert "no_short_sentence" in codes(OB.opening_issues({"hook": GOOD_HOOK, "context": long_sentences}, story(), "en"))


@pytest.mark.parametrize("language,speed,stability", [
    ("en", 1.1, 0.5), ("de", 1.155, 0.5), ("ar", 1.155, 0.5), ("es", 0.99, 0.3), ("it", 0.99, 0.3), ("fr", 1.1, 0.3),
    ("pt", 1.1, 0.3), ("tr", 1.09, 0.3), ("fa", 1.2, 0.2)])
def test_the_voice_of_the_block_is_the_languages_own_made_faster_and_less_stable(language, speed, stability):
    assert OB.post_speed_for(language) == speed
    assert OB.voice_settings_for(language)["stability"] == stability


def test_a_stability_below_the_threshold_is_kept():
    own = ai_config.voice.settings_for("v3_neutral", "fa")["stability"]
    assert own < ai_config.opening_block_settings().voice.stability_threshold and OB.voice_settings_for("fa")["stability"] == own


def test_the_music_prompt_is_instrumental_and_has_a_fallback():
    assert "no vocals" in OB.music_prompt({"music": {"prompt": "slow cello drone"}}) and "slow cello drone" in OB.music_prompt({"music": {"prompt": "slow cello drone"}})
    assert ai_config.opening_block_settings().music.mood_fallback in OB.music_prompt({})


SCENES = ["A suburban brick house at night in fresh snow, its garage door standing wide open and spilling a narrow beam of warm light across the dark driveway.",
          "A long empty hallway inside a quiet house at night, one bedroom door ajar at the far end with a faint cool glow and long shadows on the floorboards."]


def test_the_directors_answer_must_name_a_hook_among_its_candidates():
    raw = {"candidates": [{"text": "a", "kind": "quote"}, {"text": "b", "kind": "moment"}], "chosen": 5, "context": "c", "scenes": SCENES}
    _, problems = OB.parse_master(raw)
    assert "no_chosen_hook" in codes(problems)
    opening, problems = OB.parse_master({**raw, "chosen": 1, "music": {"mood": "Dread", "prompt": "p"}})
    assert opening["hook"] == "b" and not problems and opening["music"]["mood"] == "dread" and opening["scenes"] == SCENES


def test_the_scenes_of_the_pictures_are_two_symbolic_places_without_a_person_a_victim_or_a_weapon():
    raw = {"candidates": [{"text": "a", "kind": "quote"}, {"text": "b", "kind": "moment"}], "chosen": 1, "context": "c"}
    assert "scene_count" in codes(OB.parse_master({**raw, "scenes": SCENES[:1]})[1])
    assert "scene_length" in codes(OB.parse_master({**raw, "scenes": ["a door", SCENES[1]]})[1])
    refused = OB.parse_master({**raw, "scenes": [SCENES[0], "A man with a pistol standing in the dark hallway of a quiet house at night, waiting."]})[1]
    assert codes(refused) == {"scene_not_symbolic"}
    assert OB.parse_native({"hook": "h", "context": "c"}, {"scenes": SCENES, "music": {}})["scenes"] == SCENES                  # a native row shows the English pictures


def test_quotes_are_what_is_said_between_marks():
    assert quotes_of('He said, "Someone broke into my house and shot her." Then nothing. „Kurz“') == ["Someone broke into my house and shot her."]


class OpeningGen:
    def __init__(self, hooks=None, contexts=None):
        self.hooks, self.contexts = list(hooks or [GOOD_HOOK]), list(contexts or [GOOD_CONTEXT])
        self.calls: list[tuple[str, dict]] = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        res = GenerationResult(text="{}", model=f"m/{role}", provider="fake")
        hook = self.hooks.pop(0) if len(self.hooks) > 1 else self.hooks[0]
        context = self.contexts.pop(0) if len(self.contexts) > 1 else self.contexts[0]
        if role == "opening_director":
            return {"candidates": [{"text": "other", "kind": "moment", "why": "w"}, {"text": hook, "kind": "quote", "why": "w"}], "chosen": 1,
                    "context": context, "music": {"mood": "tension", "prompt": "low cello drone"}, "scenes": SCENES}, res
        return {"hook": hook, "context": context}, res


def test_the_director_makes_the_english_opening_and_repairs_what_the_checks_refuse(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = OpeningGen(hooks=["Hello and welcome to the channel.", GOOD_HOOK])
    monkeypatch.setattr("app.agents.opening.get_generation_provider", lambda: gen)
    row = asyncio.run(OpeningWriter().create_master(db_session, case, master))
    opening = json.loads(row.opening_json)
    assert row.kind == "master" and row.language == "en" and row.status == "valid" and opening["hook"] == GOOD_HOOK
    assert json.loads(row.validation_json)["repair_iterations"] == 1
    first, second = gen.calls[0][1], gen.calls[1][1]
    assert "problems_to_fix" in second and second["previous"]["chosen"] == 1
    assert first["story_first_act"] == story().acts[0].text and all("Wexford" not in str(v) for k, v in first.items() if k != "later_titles")
    assert first["later_titles"][-1] == "What Remains" and first["limits"]["candidates"] == 4


def test_an_opening_that_stays_refused_is_stored_for_review(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = OpeningGen(hooks=["Hello and welcome to the channel."])
    monkeypatch.setattr("app.agents.opening.get_generation_provider", lambda: gen)
    row = asyncio.run(OpeningWriter().create_master(db_session, case, master))
    assert row.status == "needs_review" and json.loads(row.validation_json)["repair_iterations"] == ai_config.opening_block_settings().repairs


def test_a_native_writer_writes_the_opening_again_from_the_english_one(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = OpeningGen()
    monkeypatch.setattr("app.agents.opening.get_generation_provider", lambda: gen)
    writer = OpeningWriter()
    english = asyncio.run(writer.create_master(db_session, case, master))
    de = _version(db_session, case, master, "en")           # the test story's words stand in for the language's own story
    de.language = "de"
    db_session.commit()
    native = asyncio.run(writer.write_native(db_session, case, de, english))
    role, payload = gen.calls[-1]
    assert role == "opening_native_writer" and payload["language"] == "de" and payload["english_opening"]["hook"] == GOOD_HOOK
    assert payload["limits"]["hook_max_words"] == OB.hook_max_words("de")
    assert native.kind == "native" and native.master_opening_id == english.id and native.language == "de"
    assert json.loads(native.opening_json)["music"] == json.loads(english.opening_json)["music"]
    assert db_session.query(FilmOpening).filter(FilmOpening.case_id == case.id).count() == 2


@pytest.mark.usefixtures("four_host_appearances")
def test_the_host_writer_is_told_what_the_opening_block_already_said(db_session, isolated_host_history, monkeypatch):
    from test_host import HostGen

    from app.agents.host import HostDirector

    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    director = HostDirector()
    row = asyncio.run(director.create_plan(db_session, case, None, master))
    db_session.add(FilmOpening(case_id=case.id, language="en", kind="master", story_version_id=master.id, status="valid",
                               opening_json=json.dumps({"hook": "Someone locked the door.", "context": "Nobody explains it."})))
    db_session.commit()
    asyncio.run(director.write(db_session, case, master, row))
    first = next(d for r, d in gen.calls if r == "host_writer" and d["segment"]["id"] == "S1")
    assert first["earlier_in_this_film"] == ["Someone locked the door. Nobody explains it."]
    assert OB.said_before(db_session, case.id, "de") == []


def test_a_latin_word_in_a_persian_opening_is_refused_and_a_voice_of_an_older_text_is_stale():
    from app.documentary import opening_media as OM
    issues = OB.opening_issues({"hook": "سلام", "context": "این یک متن است sage"}, story("fa"), "fa")
    assert any(i["type"] == "foreign_script" and i["quote"] == "sage" for i in issues)
    voices = {"hook": {"text": "old"}, "context": {"text": "same"}}
    assert OM.stale_voices({"hook": "new", "context": "same"}, voices) == ["hook"] and OM.stale_voices({"hook": "old", "context": "same"}, voices) == []
    assert OM.stale_voices({"hook": "x"}, {}) == ["hook"]

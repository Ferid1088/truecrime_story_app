"""The words the avatar speaks (the opening block, the short clips) are written with the punctuation rules of the language's voice director, the part the host's writer
gets too: no new agent sets their commas afterwards."""

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.documentary import host as H


def test_the_prompt_for_spoken_words_carries_the_punctuation_rules_and_the_notes_of_the_language():
    for name in ("documentary/opening/native_system", "documentary/opening/director_system"):
        text = H.voiced_prompt(name, "fa")
        assert text.startswith(prompt(name)) and "PUNCTUATION FOR THE VOICE" in text
        assert ai_config.voice_performance.punctuation_notes["fa"] in text
        assert "your words are voiced as they are written, so set the punctuation too" in text      # the host's wording: no agent edits the marks afterwards


def test_a_language_without_punctuation_rules_gets_the_prompt_as_it_is(monkeypatch):
    monkeypatch.setattr(ai_config.voice_performance, "punctuation_languages", ["en"])
    assert H.voiced_prompt("documentary/opening/native_system", "fa") == prompt("documentary/opening/native_system")


def test_the_pack_carries_the_punctuation_rules_of_each_languages_voice_director(db_session):
    from pack_fixture import PackGen  # noqa: F401
    from test_host import _plan_story

    from app.shortform import pack as PK

    case, master, _ = _plan_story(db_session)
    given = PK.dossier(PK.story_of(db_session, case, master))
    assert ai_config.voice_performance.punctuation_notes["fa"] in given["languages"]["fa"]["punctuation"]
    for code in given["languages"]:
        assert (given["languages"][code]["punctuation"] is not None) == (code in ai_config.voice_performance.punctuation_languages)

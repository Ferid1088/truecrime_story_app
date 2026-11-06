"""The host's words must be about something concrete, not abstract meta-talk; they must not repeat a reaction; the outro speaks from the end of the story; the
Persian must not read like a translation. The rules are in the prompts (writer, craft, critic) and in the checks of code."""

from __future__ import annotations

import pytest

from app.core.ai_config import ai_config
from app.documentary import host as H


@pytest.mark.parametrize("language", ["fa", "ar", "en", "de"])
def test_the_writer_is_told_to_build_on_one_concrete_thing_and_that_the_settled_is_settled(language):
    system = H.writer_system_prompt(language)
    assert "SOMETHING CONCRETE" in system and "ONE concrete thing" in system and "No abstract talk" in system
    assert "What the story has answered is settled" in system and "you stand after the last act and know how the story ended" in system
    assert H.persona_prompt(ai_config.host) in system


def test_the_persian_writer_is_warned_against_translated_thoughts_and_shown_the_difference_with_invented_examples():
    craft = H.craft_of("fa")
    assert "must not read like a translation" in craft and "به چی تکیه می‌کنی" in craft and "invented case" in craft
    assert "کالب" not in craft and "اشلی" not in craft, "an example from a real case would leak into another"


def test_the_register_note_gives_no_reaction_word_to_copy():
    note = ai_config.host.language_rules["fa"].register_note
    assert "«عجیبه.»" not in note and "never one an earlier appearance used" in note


def test_the_critic_judges_concreteness_and_what_is_settled():
    assert {"concrete", "settled"} <= set(H.CHECKS)
    from app.core.prompts import prompt

    critic = prompt("documentary/host/critic_system_prompt")
    assert "- concrete:" in critic and "- settled:" in critic and '"concrete": true, "settled": true' in critic and "all\nseventeen" in critic
    verdict = H.critic_verdict({"checks": {"concrete": False}})
    assert verdict["pass"] is False and verdict["checks"]["settled"] is True


def test_a_reaction_another_appearance_already_used_goes_back_to_the_writer():
    seg = {"target_seconds": 5, "position": "mid", "role": "transition"}
    other = "برای من اون در مهمه. عجیبه. تو چی فکر می‌کنی؟"
    issues = H.dialogue_issues(seg, "تو اون شب رو یادته؟ عجیبه! بعدش چی شد؟", "fa", [], [other])
    assert any(i["type"] == "repeated_short_sentence" and i["quote"] == "عجیبه" for i in issues)
    assert not any(i["type"] == "repeated_short_sentence" for i in H.dialogue_issues(seg, "تو اون شب رو یادته؟ صبر کن. بعدش چی شد؟", "fa", [], [other]))

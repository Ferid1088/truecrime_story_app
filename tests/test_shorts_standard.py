"""The shared creative standard for Shorts/Reels hooks and narration: the 144 scripts reach every agent that writes one, the empty suspense phrases are refused in every language, and the Shorts
aim at sixteen seconds with a hook that may be a whole sentence (the pack's own tests are in test_creative_pack.py)."""

import asyncio

import pytest

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.naming import standard as STD
from app.shortform import clips as C

LANGUAGES = ("fa", "en", "de", "es", "tr", "fr", "it", "pt", "ar")


def test_the_library_holds_all_144_scripts_two_in_each_of_eight_categories_for_each_language():
    library = STD.shorts_library()
    assert tuple(library) == LANGUAGES
    for code in LANGUAGES:
        assert len(library[code]) == 8 and all(len(examples) == 2 for examples in library[code].values()), code
    assert sum(len(e) for cats in library.values() for e in cats.values()) == 144


def test_the_shorts_text_carries_the_workflow_the_quality_rejection_and_every_script():
    text = STD.shorts_text()
    for marker in ("ROLE: You are the specialized writer", "HOOK LENGTH: Do NOT force the hook", "WORKFLOW: 1) Identify", "QUALITY REJECTION", "FINAL NOTE"):
        assert marker in text, marker
    assert all(e["text"] in text and e["title"] in text for cats in STD.shorts_library().values() for ex in cats.values() for e in ex)


class Capture:
    def __init__(self):
        self.system = None

    async def generate_structured(self, role, system, user, images=None):
        self.system = system
        return {}, None


@pytest.mark.parametrize("name", ai_config.shorts_standard.agents)
def test_every_agent_of_the_shorts_standard_gets_all_scripts_before_its_own_task(name):
    gen = Capture()
    asyncio.run(run_agent(name, gen, "{}", system="OWN TASK"))
    assert STD.shorts_text() in gen.system and gen.system.endswith("OWN TASK\n\n" + STD.CLOSING) and STD.TASK_MARK in gen.system


def test_the_short_clips_aim_at_sixteen_seconds_and_the_hook_may_be_a_whole_sentence():
    cfg = ai_config.short_clips_settings()
    assert cfg.target_seconds == 16 and sum(s.seconds for s in cfg.segments.values()) == 16 and cfg.hook_max_words >= 20 and cfg.hook_max_sentences == 2


@pytest.mark.parametrize("language, phrase", [("en", "What really happened that night?"), ("fa", "شواهد پرسش‌های تازه‌ای ایجاد کرد."), ("de", "Die Beweise warfen neue Fragen auf."), ("es", "Nadie esperaba lo que ocurrió después."),
                                              ("tr", "O gece gerçekte ne oldu?"), ("fr", "Personne ne s'attendait à ce qui s'est passé ensuite."), ("it", "Un dettaglio misterioso ha cambiato tutto."),
                                              ("pt", "O que realmente aconteceu naquela noite?"), ("ar", "ماذا حدث فعلاً في تلك الليلة؟")])
def test_an_empty_suspense_phrase_is_refused_in_every_language_but_not_when_a_concrete_fact_surrounds_it(language, phrase):
    assert C.generic_phrase_in(phrase, language) is not None
    long = "On the cruise Ashley found the messages and the texts from March 2024 that showed what really happened that night with Botner and nobody had told her"
    assert C.generic_phrase_in(long, "en") is None

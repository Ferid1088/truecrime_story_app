"""The shared creative standard for titles, Shorts/Reels titles, captions, tags and cover text: every agent that names, judges or words one gets all of it with every example, and the creative agents run
on GPT-6 alone, each answer checked once more by the same model in the same request (the second look)."""

import asyncio
import json

import pytest

from app.agents.runner import agent_def, run_agent
from app.core.ai_config import ai_config, load_ai_config
from app.naming import standard as STD

LANGUAGES = ("fa", "en", "de", "es", "tr", "fr", "it", "pt", "ar")


def test_the_library_holds_all_630_examples_in_seven_categories_of_ten_for_each_of_the_nine_languages():
    library = STD.library()
    assert tuple(library) == LANGUAGES
    for code in LANGUAGES:
        assert len(library[code]) == 7 and all(len(examples) == 10 for examples in library[code].values()), code
    assert sum(len(e) for cats in library.values() for e in cats.values()) == 630


def test_the_standard_text_carries_every_rule_the_deliverable_the_genre_terms_and_every_example():
    text = STD.standard_text()
    for marker in ("MANDATORY RULES FOR EVERY LANGUAGE AND FORMAT", "8. Preserve all case-review gates", "DELIVERABLE FOR EACH LANGUAGE", "GPT-6 criticism and final verdict", "پرونده جنایی، داستان جنایی، جنایت واقعی",
                   "PT-BR: caso criminal", "IMPORTANT FINAL REMINDER"):
        assert marker in text, marker
    examples = [e for cats in STD.library().values() for ex in cats.values() for e in ex]
    assert len(examples) == 630 and all(e in text for e in examples)


class Capture:
    def __init__(self):
        self.systems = {}

    async def generate_structured(self, role, system, user, images=None):
        self.systems[role] = system
        return {}, None


@pytest.mark.parametrize("name", ai_config.title_standard.agents)
def test_every_agent_of_the_standard_is_defined_and_gets_the_whole_standard(name):
    gen = Capture()
    agent_def(name)
    system = "OWN PROMPT"
    asyncio.run(run_agent(name, gen, "{}", system=system))
    sent = next(iter(gen.systems.values()))
    assert sent.endswith("OWN PROMPT\n\n" + STD.CLOSING) and sent.startswith(STD.standard_text())


def test_an_agent_outside_the_list_gets_its_own_prompt_only():
    gen = Capture()
    asyncio.run(run_agent("research.extract_facts", gen, "{}", system="OWN PROMPT"))
    assert next(iter(gen.systems.values())) == "OWN PROMPT"


def test_the_agents_of_every_part_are_on_the_list():
    assert {"case_naming_agent", "cover_planner", "publishing_metadata_writer",
            "tag_generator", "creative_pack"} <= set(ai_config.title_standard.agents)
    assert (ai_config.covers_settings().title["min_words"], ai_config.covers_settings().title["max_words"]) == (7, 18)


def test_the_creative_agents_of_titles_shorts_covers_and_packaging_run_on_gpt_6_alone():
    for role in ("case_naming_agent", "creative_pack", "cover_planner", "publishing_metadata_writer", "publishing_planner", "tag_generator", "opening_director", "opening_native_writer"):
        assert ai_config.alias_for(role) == "sol", role
        assert (role in load_ai_config().second_look.agents) == (role != "creative_pack"), role          # the pack checks itself twice inside its one request


class Twice:
    """A model that answers the same request twice: its first answer, then its second look at it."""

    def __init__(self, second=None):
        self.calls, self.second = [], second

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append(json.loads(user))
        if "your_first_answer" in self.calls[-1]:
            return self.second, None
        return {"titles": ["first"]}, None


def test_with_a_second_look_the_same_model_checks_its_own_answer_and_the_final_one_is_returned(monkeypatch):
    monkeypatch.setattr(ai_config.second_look, "agents", ["case_naming_agent"])
    gen = Twice({"titles": ["final"]})
    answer, _ = asyncio.run(run_agent("case_naming_agent", gen, json.dumps({"count": 1}), system="OWN"))
    assert answer == {"titles": ["final"]} and len(gen.calls) == 2
    again = gen.calls[1]
    assert json.loads(again["request"]) == {"count": 1} and again["your_first_answer"] == {"titles": ["first"]} and "SECOND LOOK" in again["second_look"]


def test_an_empty_second_answer_leaves_the_first_and_an_agent_outside_the_list_is_asked_once(monkeypatch):
    monkeypatch.setattr(ai_config.second_look, "agents", ["case_naming_agent"])
    gen = Twice({})
    answer, _ = asyncio.run(run_agent("case_naming_agent", gen, "{}", system="OWN"))
    assert answer == {"titles": ["first"]} and len(gen.calls) == 2
    once = Twice({"titles": ["final"]})
    answer, _ = asyncio.run(run_agent("thumbnail_critic", once, "{}", system="OWN"))
    assert answer == {"titles": ["first"]} and len(once.calls) == 1

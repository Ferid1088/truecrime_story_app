"""The recurring on-screen host Fereidoun (persona_master_prompt.md): the structure plan, the
seconds, the knowledge cutoff of every appearance, the dialogue checks, memory, the
director/writer/critic loop and the API."""
import asyncio
import json
import random
from types import SimpleNamespace

import pytest

from app.core.ai_config import HostConfig, ai_config
from app.documentary.host import (
    dialogue_issues, estimate_seconds, persona_prompt, target_words, word_range,
)
from app.documentary.host_firewall import (
    clean_question, later_only_terms, open_questions, reveal_issues, riddles, segment_context,
    visible_facts,
)
from app.documentary.host_planning import (
    appearance_seconds, seconds_between, story_seconds, validate_host_plan,
)
from app.documentary.host_story import (
    HostEntity, split_acts, story_from_blueprint, story_from_plan,
)
from app.providers.generation.base import GenerationResult
from test_blueprint_performance import _good, _no_contradiction

BP = _no_contradiction(_good())  # B01 hook, B03 reveal, F001..F004

_VOCAB = ("timeline witness record door morning house letter neighbour phone kitchen "
          "statement detail question street evening car window answer silence family "
          "account version hour moment report note road garden name").split()
# every sentence of the test story carries these words: none of them is "rare"
_COMMON = " ".join(_VOCAB)

PLAN = {
    "title": "The Locked Door Case",
    "central_question": "Who locked the door, and what did the court conclude?",
    "acts": [
        {"id": "act1", "title": "The Locked Door", "purpose": "the hook", "target_words": 1200,
         "reveals": ["F001"], "open_loops": ["Who locked the door? (Opened act 1, answered in act 4.)"],
         "resolved_loops": []},
        {"id": "act2", "title": "The Household", "purpose": "who lived there", "target_words": 1500,
         "reveals": ["F002"], "open_loops": ["Why was the kettle still warm?"],
         "resolved_loops": []},
        {"id": "act3", "title": "What the Police Found", "purpose": "the evidence",
         "target_words": 2000, "reveals": ["F003"], "open_loops": [], "resolved_loops": []},
        {"id": "act4", "title": "Her Account", "purpose": "the answers", "target_words": 1800,
         "reveals": ["F004"], "open_loops": [],
         "resolved_loops": ["Who locked the door?", "Why was the kettle still warm?"]},
        {"id": "act5", "title": "The Inquest", "purpose": "the hearing", "target_words": 1500,
         "reveals": [], "open_loops": ["Would the jury believe her?"], "resolved_loops": []},
        {"id": "act6", "title": "What Remains", "purpose": "the end", "target_words": 1000,
         "reveals": [], "open_loops": [], "resolved_loops": []},
    ],
}
RARE = {"act1": "Hargrove lantern", "act2": "kettle Pemberton", "act3": "ledger Marlowe",
        "act4": "Wexford locksmith", "act5": "inquest Calloway", "act6": "orchard Thorne"}
def _act_text(act_id: str, words: int) -> str:
    """Two paragraphs of `words` words in all; every one carries the common vocabulary, the
    act's rare words only once."""
    paragraph = (words - 10) // 2 // len(_VOCAB)
    body = " ".join(_COMMON for _ in range(paragraph))
    return f"{RARE[act_id]} {body}.\n\n{RARE[act_id].split()[0]} again, {body}."


TEXTS = {a["id"]: _act_text(a["id"], a["target_words"]) for a in PLAN["acts"]}
FACT_CLAIMS = {"F001": "The door was locked from the inside by Hargrove.",
               "F002": "Pemberton lived in the house.",
               "F003": "Marlowe kept a ledger of the debts.",
               "F004": "Wexford changed the lock."}
PACK = {"facts": [{"id": i, "claim": c, "category": "fact", "uncertain": False, "speaker": None}
                  for i, c in FACT_CLAIMS.items()], "timeline": [], "contradictions": []}
CASE = SimpleNamespace(canonical_title="The Locked Door Case")


ENTITIES = (HostEntity(("Hargrove",), ("F001",)), HostEntity(("Pemberton",), ("F002",)),
            HostEntity(("Marlowe",), ("F003",)), HostEntity(("Wexford", "Wexfords"), ("F004",)))


def story(language="en"):
    return story_from_plan(CASE, PLAN, TEXTS, language, PACK, ENTITIES)


def say(n: int, seed: int = 0, language: str = "en") -> str:
    """n spoken words that pass the checks: a short question to the viewer first, direct
    address, the rest common vocabulary."""
    heads = {"en": ["Can you see it?", "Did you notice that?", "Would you stop here?",
                    "Do you remember this?"],
             "de": ["Siehst du es?", "Hast du das gesehen?", "Weißt du noch?", "Glaubst du das?"],
             "fa": ["تو هم دیدی؟", "می‌دونی چرا؟", "ببین، تو چی فکر می‌کنی؟", "یادت هست؟"],
             "ar": ["هل تعرف السبب؟", "ماذا تتذكر أنت؟", "هل تتذكر ذلك؟", "تخيل معي، هل ترى ذلك؟"]}
    head = heads[language][seed % 4]
    rng = random.Random(seed)
    return head + " " + " ".join(rng.choice(_VOCAB) for _ in range(max(n - len(head.split()), 1))) + "."


def plan_raw(**over) -> dict:
    raw = {"notes": "n", "segments": [
        {"id": "S1", "position": "opening", "role": "opening", "after_act": None, "reason": "hook"},
        {"id": "S2", "position": "mid", "role": "transition", "after_act": "act2", "reason": "pause"},
        {"id": "S3", "position": "mid", "role": "viewer_question", "after_act": "act5",
         "reason": "carry a question"},
        {"id": "S4", "position": "final", "role": "outro", "after_act": None, "reason": "close"}]}
    raw.update(over)
    return raw


def codes(items):
    return {i["code"] for i in items}


# ---------------------------------------------------------------------------
# persona
# ---------------------------------------------------------------------------


def test_persona_prompt_is_the_master_prompt():
    assert "never compete with the case" in persona_prompt().lower()


def test_the_persona_is_a_friend_who_speaks_informally_in_every_language():
    persona = persona_prompt()
    assert "close friend, never like a reporter" in persona
    for rule in ("informal \"you\"", "\"du\" for one viewer", "never \"Sie\"", "تو", "never formal \"شما\"",
                 "singular", "Greeting filler", "Never give away the outcome", "You know only what the viewer knows"):
        assert rule in persona, rule


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def _cfg(**over) -> HostConfig:
    return ai_config.host.model_copy(update=over)


def test_host_config_has_separate_speaking_rates_and_rules_for_every_language():
    cfg = ai_config.host
    assert set(cfg.words_per_minute) == set(cfg.language_rules) == {"en", "de", "fa", "ar"}
    assert len(set(cfg.words_per_minute.values())) == 4
    for lang, wpm in cfg.words_per_minute.items():
        # the host talks faster than the narrator of the same language
        assert wpm > ai_config.language_pipeline.words_per_minute[lang]


def test_host_config_rejects_inconsistent_values():
    base = ai_config.host.model_dump()
    for change in ({"time_accounting": "somewhere"}, {"min_appearances": 9},
                   {"words_per_minute": {"en": 150}}):
        with pytest.raises(ValueError):
            HostConfig(**{**base, **change})
    roles = {k: dict(v) for k, v in base["roles"].items()}
    roles["opening"]["max_seconds"] = 500
    with pytest.raises(ValueError, match="max_appearance_seconds"):
        HostConfig(**{**base, "roles": roles})
    roles = {k: dict(v) for k, v in base["roles"].items() if k != "outro"}
    with pytest.raises(ValueError, match="outro"):
        HostConfig(**{**base, "roles": roles})


def test_time_accounting_adds_the_host_or_counts_it_inside_the_requested_minutes(monkeypatch):
    assert ai_config.host.time_accounting == "added"
    assert ai_config.host.story_minutes(60) == 60.0
    assert ai_config.language_pipeline.minutes_range(60) == (60.0, 70.0)
    inside = _cfg(time_accounting="inside_requested")
    assert inside.story_minutes(60) == pytest.approx(60 - min(240, 0.08 * 3600) / 60)
    monkeypatch.setattr(ai_config, "host", inside)
    low, high = ai_config.language_pipeline.minutes_range(60)
    assert low == pytest.approx(56.0) and high == pytest.approx(66.0)


# ---------------------------------------------------------------------------
# the story as the host sees it
# ---------------------------------------------------------------------------


def test_story_is_cut_on_act_markers_and_knows_what_each_act_reveals():
    marked = "\n\n".join(f"[[ACT:{a}]]\n\n{t}" for a, t in TEXTS.items())
    assert split_acts(marked) == TEXTS and split_acts("no markers at all") == {}
    s = story()
    assert s.act_ids == list(RARE)
    assert s.acts[0].hook and not s.acts[1].hook
    assert [a.payoff for a in s.acts] == [False, False, False, True, False, False]
    assert s.visible_evidence_ids(3) == ["F001", "F002", "F003"] and s.visible_evidence_ids(0) == []
    with pytest.raises(ValueError, match="no act marker"):
        story_from_plan(CASE, PLAN, {"act1": "x"}, "en", PACK)


def test_a_blueprint_story_uses_its_beats_as_acts():
    texts = {b["id"]: f"text of {b['id']}" for b in BP["beats"]}
    s = story_from_blueprint(CASE, BP, texts, "en", PACK)
    assert s.act_ids == [b["id"] for b in BP["beats"]]
    assert s.acts[0].hook and any(a.payoff for a in s.acts)


# ---------------------------------------------------------------------------
# the plan: structure only, seconds computed
# ---------------------------------------------------------------------------


def test_a_valid_structure_gets_computed_seconds_and_cutoffs():
    plan, rep = validate_host_plan(plan_raw(), story())
    assert rep["status"] == "valid", rep
    assert [s["role"] for s in plan["segments"]] == ["opening", "transition", "viewer_question", "outro"]
    assert [s["cutoff"] for s in plan["segments"]] == [0, 2, 5, 6]
    assert [s["beat_id"] for s in plan["segments"]] == [None, "act2", "act5", "act6"]
    for seg in plan["segments"]:
        spec = ai_config.host.roles[seg["role"]]
        assert spec.min_seconds <= seg["target_seconds"] <= spec.max_seconds
    # the director's own seconds are ignored: only structure is read
    raw = plan_raw()
    raw["segments"][0]["target_seconds"] = 999
    assert validate_host_plan(raw, story())[0]["segments"][0]["target_seconds"] == \
        plan["segments"][0]["target_seconds"]
    assert rep["host_seconds"] == round(sum(s["target_seconds"] for s in plan["segments"]), 1)


def test_the_number_of_appearances_is_not_fixed():
    two = plan_raw(segments=[plan_raw()["segments"][0], plan_raw()["segments"][3]])
    plan, rep = validate_host_plan(two, story())
    assert rep["status"] == "valid" and len(plan["segments"]) == 2
    one = plan_raw(segments=[plan_raw()["segments"][0]])
    assert "appearance_count_out_of_range" in codes(validate_host_plan(one, story())[1]["errors"])
    tight = _cfg(max_appearances=3)
    _, rep = validate_host_plan(plan_raw(), story(), tight)
    assert "appearance_count_out_of_range" in codes(rep["errors"])


def test_structure_errors():
    raw = plan_raw()
    raw["segments"][1]["role"] = "monologue"
    raw["segments"][2]["position"] = "final"
    _, rep = validate_host_plan(raw, story())
    assert {"unknown_role", "role_position_mismatch"} <= codes(rep["errors"])

    raw = plan_raw()
    raw["segments"][1]["after_act"] = "act1"             # right after the hook
    _, rep = validate_host_plan(raw, story())
    assert "interrupts_hook" in codes(rep["errors"])
    raw = plan_raw()
    raw["segments"][1]["after_act"] = "act3"             # right before the payoff act4
    _, rep = validate_host_plan(raw, story())
    assert "interrupts_before_payoff" in codes(rep["errors"])
    raw = plan_raw()
    raw["segments"][2]["after_act"] = "act6"             # after the last act: that is the outro
    _, rep = validate_host_plan(raw, story())
    assert "mid_after_last_act_use_outro" in codes(rep["errors"])
    raw = plan_raw()
    raw["segments"][1]["after_act"] = "act99"
    assert "unknown_act" in codes(validate_host_plan(raw, story())[1]["errors"])
    raw = plan_raw()
    raw["segments"] = raw["segments"][1:]                # no opening
    assert "needs_exactly_one_opening" in codes(validate_host_plan(raw, story())[1]["errors"])
    raw = plan_raw()
    raw["segments"] = raw["segments"][:3]                # no outro
    assert "needs_exactly_one_outro" in codes(validate_host_plan(raw, story())[1]["errors"])
    raw = plan_raw()
    raw["segments"][2]["id"] = "S2"
    assert "duplicate_segment_ids" in codes(validate_host_plan(raw, story())[1]["errors"])


def test_spacing_in_acts_and_in_story_seconds():
    raw = plan_raw()
    raw["segments"][2]["after_act"] = "act2"             # two mids after the same act
    _, rep = validate_host_plan(raw, story())
    assert "appearances_too_close" in codes(rep["errors"])
    wide = _cfg(min_acts_between=0, min_gap_seconds=1500)
    raw = plan_raw()
    raw["segments"][2]["after_act"] = "act3"
    _, rep = validate_host_plan(raw, story(), wide)
    assert "appearances_too_close_in_time" in codes(rep["errors"])
    first_two = story().acts[0].words + story().acts[1].words
    assert seconds_between(story(), 0, 2) == pytest.approx(first_two / 150 * 60)


def test_a_short_film_needs_only_the_short_film_gap_between_appearances():
    short_texts = {a["id"]: _act_text(a["id"], a["target_words"] // 20) for a in PLAN["acts"]}
    short = story_from_plan(CASE, PLAN, short_texts, "en", PACK)
    raw = plan_raw()
    raw["segments"][2]["after_act"] = "act3"
    far = _cfg(min_acts_between=0, min_gap_seconds=1500, short_film_min_gap_seconds=1)
    _, rep = validate_host_plan(raw, short, far)
    assert "appearances_too_close_in_time" not in codes(rep["errors"])      # a short film: 1 s
    _, rep = validate_host_plan(raw, story(), far)                           # a real film: 1500 s
    assert "appearances_too_close_in_time" in codes(rep["errors"])
    strict = _cfg(min_acts_between=0, short_film_min_gap_seconds=1500)
    _, rep = validate_host_plan(raw, short, strict)
    assert "appearances_too_close_in_time" in codes(rep["errors"])
    assert ai_config.host.min_gap_for(300) == ai_config.host.short_film_min_gap_seconds
    assert ai_config.host.min_gap_for(3600) == ai_config.host.min_gap_seconds
    from app.documentary.host import director_system_prompt

    assert "at least 90 seconds" in director_system_prompt(narration_seconds=420)


def test_host_time_caps_and_share():
    capped = _cfg(max_total_seconds=60)
    _, rep = validate_host_plan(plan_raw(), story(), capped)
    assert "host_time_too_long" in codes(rep["errors"])
    narrow = _cfg(max_total_share=0.01)
    _, rep = validate_host_plan(plan_raw(), story(), narrow)
    assert "host_share_too_high" in codes(rep["errors"])      # a real film (3600 s)
    short_texts = {a["id"]: _act_text(a["id"], a["target_words"] // 20) for a in PLAN["acts"]}
    short = story_from_plan(CASE, PLAN, short_texts, "en", PACK)
    _, rep = validate_host_plan(plan_raw(), short, narrow)
    assert "host_share_too_high" in codes(rep["warnings"]) and "host_share_too_high" not in codes(rep["errors"])
    off = _cfg(final_enabled=False)
    _, rep = validate_host_plan(plan_raw(), story(), off)
    assert "outro_is_off" in codes(rep["errors"])


def test_seconds_follow_role_act_weight_and_place_in_the_film():
    s = story()
    opening = appearance_seconds(s, "opening", 0)
    outro = appearance_seconds(s, "outro", 6)
    transition = appearance_seconds(s, "transition", 2)
    assert transition < outro and transition < opening        # transitions are short
    # the heavier the act before it, the longer the same role speaks
    after_heavy = appearance_seconds(s, "reflection", 3)      # act3: 2000 words
    after_light = appearance_seconds(s, "reflection", 1)      # act1: 1200 words
    assert after_heavy > after_light
    # later in the film = longer (position_gain > 0)
    flat = _cfg(act_weight_gain=0.0)
    assert appearance_seconds(s, "reflection", 5, flat) > appearance_seconds(s, "reflection", 1, flat)
    # never outside the role's bounds or the global cap
    huge = _cfg(act_weight_gain=10.0, position_gain=1.0)
    spec = huge.roles["reflection"]
    assert appearance_seconds(s, "reflection", 3, huge) == spec.max_seconds
    lone = _cfg(act_weight_gain=10.0, position_gain=-1.0)
    assert appearance_seconds(s, "reflection", 6, lone) == spec.min_seconds
    assert story_seconds(s) == pytest.approx(s.total_words() / 150 * 60) and s.total_words() > 8000


# ---------------------------------------------------------------------------
# length follows time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "de", "fa", "ar"])
def test_words_per_segment_are_seconds_times_the_languages_host_rate(language):
    wpm = ai_config.host.words_per_minute[language]
    assert target_words(30, language) == round(30 * wpm / 60)
    lo, hi = word_range(30, language)
    assert lo < target_words(30, language) < hi
    tol = ai_config.host.word_tolerance
    assert lo == int(30 * wpm / 60 * (1 - tol)) and hi == int(30 * wpm / 60 * (1 + tol)) + 1
    text = " ".join(["w"] * target_words(30, language))
    assert abs(estimate_seconds(text, language) - 30) < 0.5


def test_the_same_seconds_need_different_words_per_language():
    words = {lang: target_words(40, lang) for lang in ("en", "de", "fa", "ar")}
    assert len(set(words.values())) == 4 and words["en"] > words["de"] > words["fa"] > words["ar"]


# ---------------------------------------------------------------------------
# the knowledge cutoff
# ---------------------------------------------------------------------------


def test_clean_question_drops_the_planners_bracketed_notes():
    assert clean_question("Who killed them? (Never solved.)") == "Who killed them?"
    assert clean_question("Was she there? (Opened act 1, tested in act 6.)") == "Was she there?"


def test_open_questions_are_only_those_raised_and_not_yet_answered():
    s = story()
    assert open_questions(s, 0) == riddles(s) == ["Who locked the door?"]
    assert open_questions(s, 2) == ["Who locked the door?", "Why was the kettle still warm?"]
    assert open_questions(s, 4) == []                     # act 4 answers both
    assert open_questions(s, 5) == ["Would the jury believe her?"]


def test_the_opening_gets_no_narration_and_no_facts_later_segments_only_their_acts():
    s = story()
    opening = segment_context(s, {"cutoff": 0})
    assert opening["story_so_far"] == [] and opening["facts"] == []
    assert opening["open_questions"] == ["Who locked the door?"]
    two = segment_context(s, {"cutoff": 2})
    assert [a["act"] for a in two["story_so_far"]] == ["act1", "act2"]
    assert [f["id"] for f in two["facts"]] == ["F002", "F001"]       # newest act first
    assert [f["id"] for f in visible_facts(s, 6, _cfg(max_facts=2))] == ["F004", "F003"]


def test_later_only_terms_are_names_places_and_numbers_nobody_has_said_yet():
    s = story()
    banned = later_only_terms(s, 2)
    assert {"marlowe", "wexford", "wexfords", "calloway", "orchard"} & banned >= {"marlowe", "wexford"}
    assert "calloway" in banned and "thorne" in banned              # capitalised words (English)
    assert not banned & {"hargrove", "pemberton", "kettle"}          # told in acts 1-2
    assert not banned & set(_VOCAB)                                  # ordinary words are free
    assert "hargrove" in later_only_terms(s, 0)                      # the opening knows no act
    de = story_from_plan(CASE, PLAN, TEXTS, "de", PACK, ENTITIES)
    assert "calloway" not in later_only_terms(de, 2)                 # no name-case rule for German
    assert "marlowe" in later_only_terms(de, 2)                      # but the glossary applies
    numbered = {**TEXTS, "act5": TEXTS["act5"] + "\n\nIn 1893 it was over."}
    assert "1893" in later_only_terms(story_from_plan(CASE, PLAN, numbered, "en", PACK), 2)


def test_ordinary_words_of_a_later_act_do_not_trip_the_second_layer():
    s = story()
    assert reveal_issues(s, 0, "What small thing did the maid believe? Can you picture it?") == []


def test_a_host_who_tells_what_comes_later_is_caught_by_the_second_layer():
    s = story()
    issues = reveal_issues(s, 2, "Honestly, I keep thinking about Marlowe. Can you see why?")
    assert [i["type"] for i in issues] == ["tells_what_comes_later"]
    assert "Marlowe" in issues[0]["quote"]
    assert reveal_issues(s, 2, "I keep thinking about the kettle. Can you see why?") == []
    assert reveal_issues(s, 3, "I keep thinking about Marlowe.") == []
    allowed = reveal_issues(s, 2, "I keep thinking about the orchard.", memory_text="the orchard")
    assert allowed == []


# ---------------------------------------------------------------------------
# dialogue checks
# ---------------------------------------------------------------------------


def seg_of(role="opening", seconds=30.0):
    spec = ai_config.host.roles[role]
    return {"id": "S1", "position": spec.position, "role": role, "target_seconds": seconds,
            "cutoff": 0, "beat_id": None}


def types(issues):
    return {i["type"] for i in issues}


def test_dialogue_length_follows_the_planned_seconds_and_stock_phrases_are_caught():
    seg = seg_of()
    lo, hi = word_range(30.0, "en")
    assert dialogue_issues(seg, say(lo + 5), "en", [], []) == []
    assert dialogue_issues(seg, say(lo - 10), "en", [], [])[0]["type"] == "too_short"
    assert dialogue_issues(seg, say(hi + 5), "en", [], [])[0]["type"] == "too_long"
    assert "too_long" in types(dialogue_issues(seg_of(seconds=15.0), say(lo + 5), "en", [], []))
    text = say(lo) + " So, what do you think? [pause]"
    assert {"stock_phrase", "not_plain_speech"} <= types(dialogue_issues(seg, text, "en", [], []))
    fa = "متوجه شدم تو " + " ".join(["خانه"] * 60) + "؟"
    assert "stock_phrase" in types(dialogue_issues(seg, fa, "fa", [], []))


def test_direct_informal_address_is_required_per_language():
    seg = seg_of("transition", 15.0)
    en_lo, _ = word_range(15.0, "en")
    plain = " ".join(["record"] * (en_lo + 2)) + "."
    assert "no_direct_address" in types(dialogue_issues(seg, plain, "en", [], []))
    de_lo, _ = word_range(15.0, "de")
    formal = "Was haben Sie dabei gedacht, du " + " ".join(["Haus"] * de_lo) + ". Haben Sie das gesehen?"
    assert "formal_address" in types(dialogue_issues(seg, formal, "de", [], []))
    informal = "Hast du das gesehen? " + " ".join(["Haus"] * (de_lo - 3)) + " und dir fällt es auf."
    assert types(dialogue_issues(seg, informal, "de", [], [])) == set()
    fa_lo, _ = word_range(15.0, "fa")
    fa_formal = "شما " + " ".join(["خانه"] * fa_lo) + " ببینید"
    got = types(dialogue_issues(seg, fa_formal, "fa", [], []))
    assert "formal_address" in got and "no_direct_address" in got
    fa_ok = "ببین، تو " + " ".join(["خانه"] * fa_lo)
    assert types(dialogue_issues(seg, fa_ok, "fa", [], [])) == set()
    ar_lo, _ = word_range(15.0, "ar")
    ar_formal = "حضراتكم " + " ".join(["البيت"] * ar_lo)
    assert {"formal_address", "no_direct_address"} <= types(dialogue_issues(seg, ar_formal, "ar", [], []))
    ar_ok = "هل تعرف " + " ".join(["البيت"] * ar_lo)
    assert types(dialogue_issues(seg, ar_ok, "ar", [], [])) == set()


def test_every_deterministic_finding_tells_the_writer_what_to_do():
    seg = seg_of()
    lo, _ = word_range(30.0, "en")
    found = dialogue_issues(seg, "Hello there. " + " ".join(["record"] * lo), "en", [], [])
    assert {"greeting_filler", "no_direct_address", "no_question_to_the_viewer"} <= types(found)
    assert all(i.get("fix") for i in found)
    leak = reveal_issues(story(), 2, "I keep thinking about Marlowe.")
    assert leak[0]["fix"]
    flat = " ".join(["You said the door was locked at nine that night by the maid."] * 4)
    fixes = " ".join(i.get("fix", "") for i in dialogue_issues(
        seg_of("transition", 15.0), flat, "en", [], []))
    assert f"at most {ai_config.host.spoken.short_sentence_words} words" in fixes


def test_the_opening_needs_a_question_a_short_first_sentence_and_no_greeting():
    seg = seg_of()
    lo, _ = word_range(30.0, "en")
    body = " ".join(["record"] * lo)
    no_question = "You know the door. " + body + "."
    assert "no_question_to_the_viewer" in types(dialogue_issues(seg, no_question, "en", [], []))
    long_first = "Can you imagine " + " ".join(["record"] * 20) + "? " + body
    assert "first_sentence_too_long" in types(dialogue_issues(seg, long_first, "en", [], []))
    greeting = "Hello and welcome, can you see it? " + body
    assert "greeting_filler" in types(dialogue_issues(seg, greeting, "en", [], []))
    assert "greeting_filler" not in types(dialogue_issues(seg_of("transition", 30.0), greeting, "en", [], []))
    de = "Hallo zusammen, siehst du das? " + " ".join(["Haus"] * word_range(30.0, "de")[0])
    assert "greeting_filler" in types(dialogue_issues(seg, de, "de", [], []))
    fa = "سلام، می‌بینی؟ " + " ".join(["خانه"] * word_range(30.0, "fa")[0])
    assert "greeting_filler" in types(dialogue_issues(seg, fa, "fa", [], []))
    # a viewer question must ask something; a transition need not
    vq = seg_of("viewer_question", 15.0)
    plain = "You saw it. " + " ".join(["record"] * word_range(15.0, "en")[0]) + "."
    assert "no_question_to_the_viewer" in types(dialogue_issues(vq, plain, "en", [], []))
    assert "no_question_to_the_viewer" not in types(
        dialogue_issues(seg_of("transition", 15.0), plain, "en", [], []))


def test_dialogue_repetition_of_recent_episodes_and_of_the_films_own_segments():
    seg = seg_of()
    lo, _ = word_range(30.0, "en")
    text = say(lo + 5, seed=1)
    recent = [{"position": "opening", "dialogue": text}]
    assert {"repeats_recent_segment", "repeated_opening"} <= types(dialogue_issues(seg, text, "en", recent, []))
    other = " ".join(text.split()[:2]) + " " + say(lo, seed=2)
    assert "same_start_as_other_segment" in types(dialogue_issues(seg, text, "en", [], [other]))
    assert "repeats_another_segment" in types(dialogue_issues(seg, text, "en", [], [text + " more"]))
    assert "metadata_callback" in types(dialogue_issues(seg, text + " In episode 27 we saw this.", "en", [], []))


# ---------------------------------------------------------------------------
# director -> writer -> critic (scripted models)
# ---------------------------------------------------------------------------


class HostGen:
    """Scripted host_director / host_writer / host_critic. `plans` are returned in order (the last
    one repeats); the critic fails each id in `fail_once` the first time it sees it; every payload
    is kept in `calls` so the tests can read exactly what each model was sent."""

    def __init__(self, plans=None, fail_once=(), writer=None, invent_once=()):
        self.plans = list(plans or [plan_raw()])
        self.fail_once = set(fail_once)
        self.invent_once = set(invent_once)
        self.writer = writer
        self.calls: list[tuple[str, dict]] = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        res = GenerationResult(text="{}", model=f"m/{role}", provider="fake")
        if role == "host_director":
            return (self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]), res
        if role.startswith("host_writer"):
            seg = data["segment"]
            language = data["language"]
            dialogue = (self.writer(data) if self.writer
                        else say(seg["target_words"], sum(map(ord, seg["id"])), language))
            return {"dialogue": dialogue, "dimensions": ["curiosity"], "memory_used": None,
                    "delivery": "direct", "transition_back": "back to the story",
                    "memory_note": {"kind": "open_question", "text": "Who locked the door?"}
                    if seg["id"] == "S1" else None}, res
        if role == "host_critic":
            seg = data["segment"]["id"]
            bad = seg in self.fail_once
            self.fail_once.discard(seg)
            return {"checks": {"friend_tone": not bad},
                    "problems": [{"check": "friend_tone", "quote": "x", "fix": "sound like a friend"}]
                    if bad else []}, res
        if role == "host_meaning_checker":
            entry = data["segments"][0]
            invented = entry["segment_id"] in self.invent_once
            self.invent_once.discard(entry["segment_id"])
            return {"segments": [{"segment_id": entry["segment_id"],
                                  "added": ["says: 'the door was red'; the source says nothing"]
                                  if invented else [], "changed": [], "certainty": []}]}, res
        raise AssertionError(f"unexpected role {role}")


def _plan_story(db, language="en", title=None):
    """A case with a story plan, a final in `language` written to it (act markers), the plan's
    facts as Fact rows."""
    from test_blueprint_performance import _story
    from app.db.models import Fact, StoryPlan, StoryVersion

    case, master = _story(db, title=title)
    for fact in db.query(Fact).filter(Fact.case_id == case.id).order_by(Fact.id):
        n = fact.id - min(f.id for f in db.query(Fact).filter(Fact.case_id == case.id))
        fact.claim = list(FACT_CLAIMS.values())[n]
    plan = StoryPlan(case_id=case.id, language="en", target_minutes=60.0, status="approved",
                     plan_json=json.dumps(PLAN))
    db.add(plan)
    db.commit()
    master.story_plan_id = plan.id
    master.language = language
    master.story_text = "\n\n".join(f"[[ACT:{a}]]\n\n{t}" for a, t in TEXTS.items())
    db.commit()
    return case, master, plan


def _version(db, case, master, language):
    from app.db.models import StoryVersion

    row = StoryVersion(case_id=case.id, version=9, kind="final", language=language,
                       narrative_angle="{}", story_plan_id=master.story_plan_id, status="ready",
                       engagement_score=0.0, story_text=master.story_text)
    db.add(row)
    db.commit()
    return row


def _fact_ids(db, case):
    from app.db.models import Fact

    return [f.id for f in db.query(Fact).filter(Fact.case_id == case.id).order_by(Fact.id)]


@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
def test_host_director_plans_the_structure_once_and_writes_every_language(
        db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector
    from app.db.models import HostMemory

    case, master, plan = _plan_story(db_session)
    bad = plan_raw()
    bad["segments"][1]["after_act"] = "act3"                     # before the payoff
    gen = HostGen(plans=[bad, plan_raw()], fail_once={"S2"})
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    director = HostDirector()

    row = asyncio.run(director.create_plan(db_session, case, None, master))
    report = json.loads(row.validation_json)
    assert row.status == "valid" and report["repair_iterations"] == 1, report
    assert row.blueprint_id is None and row.story_plan_id == plan.id
    plan_in = gen.calls[0][1]
    assert set(plan_in) == {"case", "film_seconds", "acts"}      # structure only: no text, no evidence
    assert all("narration" not in a and "evidence" not in a for a in plan_in["acts"])
    assert "errors_to_fix" in gen.calls[1][1]
    segs = json.loads(row.plan_json)["segments"]
    assert [(s["role"], s["cutoff"]) for s in segs] == [
        ("opening", 0), ("transition", 2), ("viewer_question", 5), ("outro", 6)]

    en = asyncio.run(director.write(db_session, case, master, row))
    out = json.loads(en.segments_json)
    assert en.status == "valid" and en.language == "en"
    assert json.loads(en.validation_json)["repair_iterations"] == 1       # S2's critic repair
    for key in ("segment_id", "placement", "role", "purpose", "personality_dimension",
                "memory_reference", "delivery_direction", "avatar_dialogue", "target_duration",
                "target_seconds", "transition_back", "cutoff"):
        assert key in out[0]
    assert out[0]["placement"].startswith("Opening") and out[1]["placement"].startswith("After act2")
    assert out[3]["placement"].startswith("Outro")
    for seg, planned in zip(out, segs):
        lo, hi = word_range(planned["target_seconds"], "en")
        assert lo <= len(seg["avatar_dialogue"].split()) <= hi
        assert seg["target_seconds"] == planned["target_seconds"]
    notes = db_session.query(HostMemory).filter_by(case_id=case.id).all()
    assert [(m.kind, m.origin) for m in notes] == [("open_question", "host_plan")]

    de_version = _version(db_session, case, master, "de")
    de = asyncio.run(director.write(db_session, case, de_version, row))
    assert de.language == "de" and de.status == "valid"
    writer_calls = [d for r, d in gen.calls if r == "host_writer"]
    assert {d["language"] for d in writer_calls} == {"en", "de"}
    # writes go in film order, one appearance per call, each seeing what the film already said
    first_en = [d for d in writer_calls if d["language"] == "en"]
    assert [d["segment"]["id"] for d in first_en] == ["S1", "S2", "S2", "S3", "S4"]   # S2 repaired once
    s3 = next(d for d in writer_calls if d["language"] == "en" and d["segment"]["id"] == "S3")
    assert len(s3["earlier_in_this_film"]) == 2
    assert [d for d in writer_calls if d["segment"]["id"] == "S1"][0]["earlier_in_this_film"] == []
    # the cutoff holds for every payload of every call (writer and critic, repairs included)
    assert_cutoff_everywhere(gen.calls, db_session, case)


def assert_cutoff_everywhere(calls, db, case):
    """Proof: no prompt payload of a segment contains anything beyond its cutoff."""
    cutoffs = {"S1": 0, "S2": 2, "S3": 5, "S4": 6}
    ids = _fact_ids(db, case)
    evidence_of_act = {"act1": [ids[0]], "act2": [ids[1]], "act3": [ids[2]], "act4": [ids[3]],
                       "act5": [], "act6": []}
    acts = list(RARE)
    checked = 0
    for role, data in calls:
        if role not in ("host_writer", "host_critic"):
            continue
        cut = cutoffs[data["segment"]["id"]]
        assert data["segment"]["acts_heard"] == cut
        blob = json.dumps(data, ensure_ascii=False)
        for act in acts[cut:]:
            n = acts.index(act) + 1
            assert RARE[act].split()[0] not in blob, f"{act} text in a payload after act {cut}"
            assert PLAN["acts"][n - 1]["title"] not in blob
            for eid in evidence_of_act[act]:
                assert f'"{eid}"' not in blob
        for act in acts[:cut]:
            assert RARE[act].split()[0] in blob                    # (and all earlier acts ARE there)
        assert [a["act"] for a in data["story_so_far"]] == acts[:cut]
        assert "(Opened act" not in blob and "answered in act" not in blob
        checked += 1
    assert checked >= 8


def test_every_segment_gets_only_its_cutoff_in_prompts_for_all_four_languages():
    """The writer and critic input of each of the plan's segments, built for each language."""
    from app.documentary.host import critic_input, writer_input

    plan, _ = validate_host_plan(plan_raw(), story())
    for language in ("en", "de", "fa", "ar"):
        s = story_from_plan(CASE, PLAN, TEXTS, language, PACK)
        for seg in plan["segments"]:
            wi = writer_input(s, seg, language, {}, [], [])
            payload = json.dumps(critic_input(wi, "dialogue"), ensure_ascii=False)
            cut = seg["cutoff"]
            for n, act in enumerate(RARE, 1):
                assert (RARE[act].split()[0] in payload) == (n <= cut), (language, seg["id"], act)
            for eid in ("F001", "F002", "F003", "F004"):
                told = [a for a in PLAN["acts"] if eid in a["reveals"]][0]["id"]
                assert (f'"{eid}"' in payload) == (int(told[3:]) <= cut)
            assert wi["segment"]["target_words"] == target_words(seg["target_seconds"], language)


def test_a_dialogue_that_leaks_a_later_act_is_sent_back_with_its_own_quote(
        db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector

    case, master, _ = _plan_story(db_session)
    seen = []

    def writer(data):
        seen.append(data)
        text = say(data["segment"]["target_words"], sum(map(ord, data["segment"]["id"])),
                   data["language"])
        if data["segment"]["id"] == "S2" and "previous_dialogue" not in data:
            return text.replace("?", " Marlowe?", 1)
        return text

    gen = HostGen(writer=writer)
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    row = asyncio.run(HostDirector().write(db_session, case, master, plan_row))
    assert row.status == "valid"
    repair = next(d for d in seen if "previous_dialogue" in d)
    problem = repair["problems_to_fix"][0]
    assert problem["type"] == "tells_what_comes_later" and "Marlowe" in problem["quote"]
    # the repair prompt itself still holds nothing from after the cutoff
    assert "ledger" not in json.dumps({k: v for k, v in repair.items()
                                       if k not in ("previous_dialogue", "problems_to_fix")})


def test_unfixable_segment_needs_review(db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector

    case, master, _ = _plan_story(db_session)
    gen = HostGen(writer=lambda d: say(d["segment"]["target_words"], 5, d["language"])
                  + " What do you think?")
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    row = asyncio.run(HostDirector().write(db_session, case, master, plan_row))
    rep = json.loads(row.validation_json)
    assert row.status == "needs_review" and rep["failed_segments"] == ["S1", "S2", "S3", "S4"]
    assert rep["repair_iterations"] == 4 * ai_config.host.max_writer_repairs
    seg = json.loads(row.segments_json)[0]
    assert "stock_phrase" in {i["type"] for i in seg["quality"]["deterministic"]}
    # the critic is only asked about a dialogue that passed the code's checks
    assert not [r for r, _ in gen.calls if r == "host_critic"]


def _scene_of(db, case, master, plan_row, segment_id, voice_seconds):
    """The first scene of the written English host segments, with a measured voice."""
    from app.agents.host import HostDirector
    from app.documentary.host_scenes import plan_host_scenes

    row = asyncio.run(HostDirector().write(db, case, master, plan_row))
    scene = next(s for s in plan_host_scenes(db, row) if s.host_segment_id == segment_id)
    scene.voice_seconds = voice_seconds
    db.commit()
    return row, scene


def test_retiming_rewrites_a_scene_from_the_same_cutoff_input_and_stores_the_new_text(
        db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector
    from app.db.models import HostSegments

    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    row, scene = _scene_of(db_session, case, master, plan_row, "S2", 30.0)
    assert scene.planned_duration == json.loads(plan_row.plan_json)["segments"][1]["target_seconds"]
    old = scene.text
    target = 18.0
    gen.writer = lambda d: say(d["segment"]["target_words"] // 2, 41, d["language"])
    calls_before = len(gen.calls)
    asyncio.run(HostDirector().retime_scene(db_session, scene, target))
    asked = gen.calls[calls_before][1]
    fix = asked["problems_to_fix"][0]
    assert fix["check"] == "length" and "30.0 s" in fix["fix"] and "18 s" in fix["fix"]
    assert "cut the least important sentences" in fix["fix"] and asked["previous_dialogue"] == old
    assert asked["segment"]["acts_heard"] == 2 and "Marlowe" not in json.dumps(asked)
    assert "Hargrove" in json.dumps(asked) and "Pemberton" in json.dumps(asked)
    assert scene.text != old and len(scene.text.split()) < len(old.split())
    stored = json.loads(db_session.get(HostSegments, row.id).segments_json)[1]
    assert stored["avatar_dialogue"] == scene.text
    assert stored["estimated_seconds"] == estimate_seconds(scene.text, "en")

    # a scene that is too short is asked to grow, from the same acts
    gen.writer = lambda d: say(d["segment"]["target_words"] * 2, 43, d["language"])
    scene.voice_seconds = 9.0
    calls_before = len(gen.calls)
    asyncio.run(HostDirector().retime_scene(db_session, scene, 20.0))
    assert "add one more thought" in gen.calls[calls_before][1]["problems_to_fix"][0]["fix"]


def test_a_retimed_text_that_loses_the_direct_address_is_repaired_inside_the_retiming(
        db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector

    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    row, scene = _scene_of(db_session, case, master, plan_row, "S2", 30.0)
    answers = []

    def writer(data):
        answers.append(data)
        words = data["segment"]["target_words"] // 2
        if len(answers) == 1:
            return " ".join(["record"] * words) + "."          # no 'you': refused by the code
        return say(words, 47, data["language"])

    gen.writer = writer
    asyncio.run(HostDirector().retime_scene(db_session, scene, 18.0))
    assert len(answers) == 2
    assert answers[1]["problems_to_fix"][-1]["type"] == "no_direct_address"
    assert "you" in scene.text.lower()


def test_retiming_can_ask_the_writer_to_avoid_words_the_voice_says_wrong(
        db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector

    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    row, scene = _scene_of(db_session, case, master, plan_row, "S2", 17.0)
    gen.writer = lambda d: say(d["segment"]["target_words"], 49, d["language"])
    calls = len(gen.calls)
    asyncio.run(HostDirector().retime_scene(db_session, scene, 17.0, ["kettle"]))
    problems = gen.calls[calls][1]["problems_to_fix"]
    assert problems[0]["check"] == "pronunciation" and problems[0]["quote"] == "kettle"
    assert "other words" in problems[0]["fix"] and problems[1]["check"] == "length"


def test_a_retimed_text_that_leaks_or_does_not_change_is_refused(
        db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector

    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    row, scene = _scene_of(db_session, case, master, plan_row, "S2", 30.0)
    same = scene.text
    gen.writer = lambda d: same
    with pytest.raises(RuntimeError, match="did not change"):
        asyncio.run(HostDirector().retime_scene(db_session, scene, 18.0))
    gen.writer = lambda d: say(d["segment"]["target_words"], 45, d["language"]).replace("?", " Marlowe?", 1)
    with pytest.raises(RuntimeError, match="tells_what_comes_later"):
        asyncio.run(HostDirector().retime_scene(db_session, scene, 18.0))
    assert scene.text == same


def test_the_host_meaning_check_runs_for_fa_and_ar_and_sends_invented_facts_back(
        db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector

    assert ai_config.host.meaning_check_languages == ["fa", "ar"]
    case, master, _ = _plan_story(db_session)
    gen = HostGen(invent_once={"S2"})
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    for language in ("en", "de"):
        version = master if language == "en" else _version(db_session, case, master, language)
        asyncio.run(HostDirector().write(db_session, case, version, plan_row))
    assert not [r for r, _ in gen.calls if r == "host_meaning_checker"]      # not for en / de
    fa = _version(db_session, case, master, "fa")
    row = asyncio.run(HostDirector().write(db_session, case, fa, plan_row))
    assert row.status == "valid" and json.loads(row.validation_json)["repair_iterations"] == 1
    checks = [d for r, d in gen.calls if r == "host_meaning_checker"]
    assert len(checks) == 5                                   # 4 segments + S2 once more
    first = checks[0]["segments"][0]
    assert first["spoken"] and first["source"].startswith("CASE:")
    # the checker's source for S2 is the story up to act 2 and nothing after it
    s2 = next(c["segments"][0] for c in checks if c["segments"][0]["segment_id"] == "S2")
    assert "Pemberton" in s2["source"] and "Marlowe" not in s2["source"]
    repair = next(d for r, d in gen.calls
                  if r == "host_writer_fa_ar" and "previous_dialogue" in d)
    assert repair["problems_to_fix"][0]["check"] == "meaning_added"
    assert "door was red" in repair["problems_to_fix"][0]["quote"]


def test_a_segment_the_meaning_checker_does_not_return_fails():
    from app.documentary.host import meaning_problems

    assert meaning_problems({"segments": []}, "S1")[0]["check"] == "meaning_unchecked"
    clean = {"segments": [{"segment_id": "S1", "added": [], "changed": [], "certainty": []}]}
    assert meaning_problems(clean, "S1") == []
    bad = {"segments": [{"segment_id": "S1", "added": [], "changed": ["1893 vs 1892"],
                         "certainty": ["alleged became fact"]}]}
    assert [p["check"] for p in meaning_problems(bad, "S1")] == ["meaning_changed",
                                                                   "meaning_certainty"]


def test_spoken_not_written_helpers():
    from app.documentary.host import spoken_issues

    cfg = ai_config.host
    flat = ["The door was locked at nine that night by the maid.",
            "The family had eaten breakfast before the morning began.",
            "The police arrived at the house after the neighbour called.",
            "The neighbour had seen nothing unusual on the street."]
    got = {i["type"] for i in spoken_issues(flat, cfg)}
    assert {"no_short_sentences", "flat_rhythm"} <= got
    lively = ["The door was locked at nine that night by the maid, and nobody argued.",
              "Locked. Really?", "I keep coming back to that.",
              "And then, hmm, the neighbour says she saw nothing, which is odd."]
    assert spoken_issues(lively, cfg) == []
    same = lively[:2] + ["Locked again, which I did not expect to hear from anyone at all."]
    assert "same_sentence_opening" in {i["type"] for i in spoken_issues(
        ["Locked up tight, they said.", "Locked up again, says someone else.", "Hmm.",
         "Who knows what happened that long night in that house."], cfg)}
    assert spoken_issues(lively[:3], cfg) == []              # too few sentences to judge


def test_a_memory_the_writer_invents_is_refused(db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector

    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    original = gen.generate_structured

    async def inventing(role, system, user, images=None):
        raw, res = await original(role, system, user)
        if role == "host_writer":
            raw["memory_used"] = "A777"
        return raw, res

    monkeypatch.setattr(gen, "generate_structured", inventing)
    row = asyncio.run(HostDirector().write(db_session, case, master, plan_row))
    seg = json.loads(row.segments_json)[0]
    assert row.status == "needs_review"
    assert seg["quality"]["deterministic"][0]["type"] == "unverified_memory"


def test_each_language_is_written_by_its_own_writer_agent_and_checked_by_an_independent_critic(
        db_session, isolated_host_history, monkeypatch):
    from app.agents.host import HostDirector

    cfg = ai_config.host
    assert cfg.writer_agents == {"en": "host_writer", "de": "host_writer",
                                 "fa": "host_writer_fa_ar", "ar": "host_writer_fa_ar"}
    routing = ai_config.generation_providers["apimaster"].routing
    aliases = ai_config.generation_providers["apimaster"].models
    model_of = {role: aliases[routing[role]] for role in
                ("host_director", "host_writer", "host_writer_fa_ar", "host_critic")}
    assert model_of["host_writer_fa_ar"] == "gpt-5.6-luna" and model_of["host_writer"] == "gpt-6.1-sol"
    assert model_of["host_critic"] not in (model_of["host_writer"], model_of["host_writer_fa_ar"],
                                           model_of["host_director"])
    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    for language in ("en", "de", "fa", "ar"):
        version = master if language == "en" else _version(db_session, case, master, language)
        row = asyncio.run(HostDirector().write(db_session, case, version, plan_row))
        assert row.status == "valid", (language, [s["quality"]["deterministic"]
                                                   for s in json.loads(row.segments_json)])
    used = {}
    for role, data in gen.calls:
        if role.startswith("host_writer"):
            used.setdefault(data["language"], set()).add(role)
    assert used == {"en": {"host_writer"}, "de": {"host_writer"},
                    "fa": {"host_writer_fa_ar"}, "ar": {"host_writer_fa_ar"}}


# ---------------------------------------------------------------------------
# API and pipeline stages
# ---------------------------------------------------------------------------


def _with_blueprint(db, case, master, status="valid"):
    from app.db.models import EditorialBlueprint

    row = EditorialBlueprint(case_id=case.id, story_version_id=master.id, status=status,
                             central_question=BP["central_question"],
                             editorial_thesis="t", human_thread="Leela",
                             blueprint_json=json.dumps(BP), story_text_hash=master.text_hash)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_a_blueprint_story_is_planned_and_written_through_its_beats(
        db_session, isolated_host_history, monkeypatch):
    from test_blueprint_performance import _story
    from app.agents.host import HostDirector

    case, master = _story(db_session)
    bp = _with_blueprint(db_session, case, master)
    wide = _cfg(min_gap_seconds=0, min_acts_between=0, max_total_share=0.5)
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    monkeypatch.setattr(HostDirector, "__init__", lambda self: (
        setattr(self, "gen", gen), setattr(self, "cfg", wide)) and None)
    monkeypatch.setattr(ai_config, "host", wide)
    raw = {"segments": [
        {"id": "S1", "position": "opening", "role": "opening", "after_act": None, "reason": "r"},
        {"id": "S2", "position": "final", "role": "outro", "after_act": None, "reason": "r"}]}
    gen = HostGen(plans=[raw])
    row = asyncio.run(HostDirector().create_plan(db_session, case, bp, master))
    assert row.blueprint_id == bp.id and row.story_plan_id is None and row.status in ("valid", "needs_review")
    assert [a["id"] for a in gen.calls[0][1]["acts"]] == [b["id"] for b in BP["beats"]]


def test_host_api(client, db_session, isolated_host_history, monkeypatch):
    from test_blueprint_performance import _story
    from app.db.models import StoryVersion

    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    # (the blueprint's four short beats cannot hold the real spacing rules)
    monkeypatch.setattr(ai_config, "host", _cfg(min_gap_seconds=0, min_acts_between=0,
                                                max_total_share=0.5))
    case, master = _story(db_session)
    assert client.post(f"/api/cases/{case.id}/documentary/host-plan").status_code == 409
    bp = _with_blueprint(db_session, case, master)
    assert client.get(f"/api/cases/{case.id}/documentary/host-plan").status_code == 404
    gen.plans = [{"segments": [
        {"id": "S1", "position": "opening", "role": "opening", "after_act": None, "reason": "r"},
        {"id": "S4", "position": "final", "role": "outro", "after_act": None, "reason": "r"}]}]
    r = client.post(f"/api/cases/{case.id}/documentary/host-plan")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert [s["position"] for s in plan["plan"]["segments"]] == ["opening", "final"]
    assert client.get(f"/api/cases/{case.id}/documentary/host-plan").json()["id"] == plan["id"]

    from app.documentary.host import beat_texts
    texts = beat_texts(master, BP)
    spoken = StoryVersion(
        case_id=case.id, version=2, kind="spoken", language="de", narrative_angle="{}",
        master_version_id=master.id, status="ready", engagement_score=0.0,
        story_text="\n\n".join(texts.values()),
        narrative_structure=json.dumps({"blueprint_id": bp.id, "sections": [
            {"id": k, "text": v} for k, v in texts.items()]}))
    db_session.add(spoken)
    db_session.commit()
    assert client.get(f"/api/documentary/versions/{spoken.id}/host").status_code == 404
    r = client.post(f"/api/documentary/versions/{spoken.id}/host")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "de" and len(body["segments"]) == 2
    assert client.get(f"/api/documentary/versions/{spoken.id}/host").json()["id"] == body["id"]
    writer_in = next(d for role, d in gen.calls if role == "host_writer")
    assert writer_in["language"] == "de"

    mems = client.get(f"/api/documentary/host/memory?case_id={case.id}").json()
    assert [m["origin"] for m in mems] == []        # a note comes only from the English writer
    m = client.post("/api/documentary/host/memory", json={
        "case_id": case.id, "kind": "correction",
        "text": "The first reading of the timeline was wrong."}).json()
    assert m["ref"] == f"M{m['id']}" and m["origin"] == "editor"
    assert client.post("/api/documentary/host/memory", json={
        "case_id": case.id, "kind": "rumour", "text": "xyz"}).status_code == 422
    off = client.patch(f"/api/documentary/host/memory/{m['id']}", json={"active": False}).json()
    assert off["active"] is False


def test_job_stages_include_the_host(monkeypatch):
    from app.documentary.jobs import plan_stages

    monkeypatch.setattr(ai_config.avatar, "enabled", True)
    names = [s["name"] for s in plan_stages(["en", "fa"])]
    assert names.index("spoken:fa") < names.index("host_plan") < names.index("visual_needs")
    assert names.index("host:fa") < names.index("performance:fa")
    monkeypatch.setattr(ai_config.host, "enabled", False)
    assert not any(n.startswith("host") for n in (s["name"] for s in plan_stages(["en"])))
    # no avatar -> nobody renders the host -> no model calls spent on it
    monkeypatch.setattr(ai_config.host, "enabled", True)
    monkeypatch.setattr(ai_config.avatar, "enabled", False)
    assert not any(n.startswith("host") for n in (s["name"] for s in plan_stages(["en"])))


def test_every_language_has_its_channel_and_studio():
    from pathlib import Path
    from app.documentary.host import writer_system_prompt

    names = {l: c.name for l, c in ai_config.channels.items()}
    assert names == {"en": "ClueVera", "de": "Fallspur", "fa": "رد خاموش", "ar": "أثر خفي"}
    assert set(ai_config.documentary.languages) <= set(names)
    from app.documentary.studio import load_registry

    root = Path(__file__).resolve().parents[1]
    reg = load_registry()
    for lang, c in ai_config.channels.items():
        assert c.studio_dir == f"data/studio/{lang}"  # one spelling, lowercase
        shots = sorted(p.name for p in (root / c.studio_dir).glob("*.png"))
        # the studio folder holds exactly the registered pictures: one master
        assert shots == sorted(a.file for a in reg.assets_of(lang))
        assert shots == ["01_overview_wide.png"]
    assert "«Fallspur»" in writer_system_prompt("de")
    assert "«رد خاموش»" in writer_system_prompt("fa")


def test_settings_show_channels_and_credential_presence_only(client, monkeypatch):
    monkeypatch.setenv("HEYGEN_API_KEY", "secret-heygen-value")
    monkeypatch.setenv("TrueCrime_Avatar_ID_Heygen", "avatar-123")
    monkeypatch.delenv("TrueCrime_ELEVENLABS_API_KEY", raising=False)
    s = client.get("/api/documentary/settings").json()
    assert s["channels"]["de"]["name"] == "Fallspur"
    assert s["credentials"] == {"elevenlabs": False, "avatar": {
        "provider": "heygen", "key_present": True, "avatar_id_present": True}}
    body = json.dumps(s)
    assert "secret-heygen-value" not in body and "avatar-123" not in body


def test_host_time_caps_are_the_owner_decision():
    assert ai_config.host.max_appearance_seconds <= 120
    assert ai_config.host.max_total_seconds == 240
    assert ai_config.host.final_enabled is True


@pytest.mark.parametrize("language", ["fa", "en", "de", "ar"])
def test_the_exact_persona_file_reaches_director_writer_and_critic_in_every_language(language):
    """Part 6f: sha256 of the persona file equals the sha256 of the persona
    block found in each rendered system prompt."""
    import hashlib
    from pathlib import Path

    from app.documentary.host import (
        critic_system_prompt, director_system_prompt, persona_sha256, writer_system_prompt,
    )

    persona = Path(ai_config.host.persona_file).read_text(encoding="utf-8").strip()
    file_sha = hashlib.sha256(persona.encode("utf-8")).hexdigest()
    assert persona_sha256() == file_sha
    for rendered in (director_system_prompt(), writer_system_prompt(language),
                     critic_system_prompt(language)):
        start = rendered.index(persona[:80])
        block = rendered[start:start + len(persona)]
        assert hashlib.sha256(block.encode("utf-8")).hexdigest() == file_sha


@pytest.mark.parametrize("language", ["fa", "en", "de", "ar"])
def test_the_prompts_carry_the_friend_rules_the_opening_rules_and_the_cutoff(language):
    from app.documentary.host import (
        critic_system_prompt, director_system_prompt, writer_system_prompt,
    )

    writer = writer_system_prompt(language)
    for phrase in ("WHAT YOU DO NOT KNOW", "story_so_far", "talking to one friend", "no greeting",
                   "the hook", "allowed_words", "BY ROLE"):
        assert phrase in writer, phrase
    assert "Sie-form" not in writer                  # German address lives in the persona only
    critic = critic_system_prompt(language)
    for check in ("direct_address", "informal_form", "friend_tone", "hook", "no_spoiler",
                  "builds_on_story"):
        assert check in critic
    director = director_system_prompt()
    assert "STRUCTURE" in director and "you do not choose seconds" in director
    assert "logo" in director


def test_a_host_call_without_the_persona_in_its_prompt_is_refused(db_session):
    from app.agents.host import HostDirector

    with pytest.raises(RuntimeError, match="persona is missing"):
        asyncio.run(HostDirector()._json(db_session, 1, "Host Critic", "host_critic",
                                         "a prompt without the persona", {}))


def test_every_host_call_records_the_persona_sha256_in_the_run_store(db_session, monkeypatch):
    from app.agents.host import HostDirector
    from app.db.models import AgentRun
    from app.documentary.host import critic_system_prompt, persona_sha256

    class Gen:
        async def generate_structured(self, role, system, user):
            return {"checks": {}}, GenerationResult(text="{}", model="m", provider="p")

        def is_configured(self):
            return True

    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: Gen())
    asyncio.run(HostDirector()._json(db_session, 1, "Host Critic", "host_critic",
                                     critic_system_prompt("fa"), {}))
    run = db_session.query(AgentRun).filter(AgentRun.agent_name == "Host Critic").order_by(
        AgentRun.id.desc()).first()
    assert f"persona_sha256={persona_sha256()}" in run.input_summary


def test_persona_hash_is_stored_changes_with_the_file_and_old_rows_say_unknown(
        db_session, tmp_path, monkeypatch):
    from app.db.models import HostPlan
    from app.documentary.host import host_plan_dict, persona_sha256

    persona = tmp_path / "persona.md"
    persona.write_text("# Persona\nversion one", encoding="utf-8")
    monkeypatch.setattr(ai_config.host, "persona_file", str(persona))
    first = persona_sha256()
    from test_blueprint_performance import _story

    case, master = _story(db_session)
    bp = _with_blueprint(db_session, case, master)
    row = HostPlan(case_id=case.id, blueprint_id=bp.id, status="valid",
                   persona_file=str(persona), persona_sha256=first)
    old = HostPlan(case_id=case.id, blueprint_id=bp.id, status="valid")
    db_session.add_all([row, old])
    db_session.commit()
    assert host_plan_dict(row)["persona_state"] == "current"
    assert host_plan_dict(old)["persona_state"] == "unknown persona"
    persona.write_text("# Persona\nversion two", encoding="utf-8")
    assert persona_sha256() != first
    assert host_plan_dict(row)["persona_state"] == "persona changed"


def test_memory_api_lists_usage_edits_deactivates_and_deletes_only_editor_memories(
        client, db_session):
    from test_blueprint_performance import _story
    from app.db.models import HostMemory, HostPlan, HostSegments

    case, master = _story(db_session)
    bp = _with_blueprint(db_session, case, master)
    editor = HostMemory(case_id=case.id, kind="theme", text="one witness", origin="editor")
    planned = HostMemory(case_id=case.id, kind="reaction", text="a reaction", origin="host_plan")
    db_session.add_all([editor, planned])
    db_session.flush()
    plan = HostPlan(case_id=case.id, blueprint_id=bp.id, status="valid", plan_json="{}")
    db_session.add(plan)
    db_session.flush()
    db_session.add(HostSegments(
        case_id=case.id, story_version_id=master.id, host_plan_id=plan.id, language="en",
        segments_json=json.dumps([{"segment_id": "S2",
                                   "memory_reference": f"M{editor.id} — Old case: one witness"}])))
    db_session.commit()
    editor_id, planned_id = editor.id, planned.id

    rows = {m["id"]: m for m in client.get(f"/api/documentary/host/memory?case_id={case.id}").json()}
    assert rows[editor_id]["used_by"][0]["segment_id"] == "S2" and rows[planned_id]["used_by"] == []
    off = client.patch(f"/api/documentary/host/memory/{planned_id}", json={"active": False}).json()
    assert off["active"] is False
    edited = client.patch(f"/api/documentary/host/memory/{planned_id}", json={"text": "better words"}).json()
    assert edited["origin"] == "editor"
    assert client.delete(f"/api/documentary/host/memory/{editor_id}").json()["deleted"] is True
    db_session.expire_all()
    assert db_session.get(HostMemory, editor_id) is None
    assert client.delete(f"/api/documentary/host/memory/{planned_id}").status_code == 200

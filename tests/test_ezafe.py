"""The Persian ezafe analyzer and what it changes downstream: the demonstrations and the held-out cases (each kind of error counted apart), -e / -ye by the
spoken final sound (a ه said h takes a kasra, never a hamza), the agents' own judgements and their disagreements, the risk signals, the planner's ezafe
mark, the memory's context, and the text the renderer sends."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from test_pronunciation_planner import MODEL, VOICE, entry_for, language, render_with  # noqa: F401  (language is a fixture)
from test_reading_agents import Script, item, no_provider  # noqa: F401  (no_provider is a fixture)

from app.db.models import PronunciationEntry, PronunciationIntervention
from app.documentary.pronunciation_memory import PronunciationMemory
from app.documentary.pronunciation_planner import Planner
from app.documentary.pronunciation_risk import RiskEngine
from app.documentary.reading import ezafe as EZ
from app.documentary.reading import stage as RS
from app.documentary.reading.answer import ReadingWord, parse
from app.documentary.reading.checker import Checker
from app.documentary.reading.spelling import persian
from app.documentary.reading.table import FilmTable
from app.documentary.reading.voicing import Voicing, WordSignal
from app.documentary.reading.words import key_of, tokenize

CASES = Path(__file__).resolve().parents[1] / "prompts" / "pronunciation" / "decision_cases"


def cases(name: str) -> list[dict]:
    return json.loads((CASES / name).read_text(encoding="utf-8"))["cases"]


def analyze(text: str, readings: list[str], opinions: dict | None = None) -> list[EZ.Boundary]:
    return EZ.EzafeAnalyzer().analyze(tokenize(text), readings, opinions)


# --- the analyzer --------------------------------------------------------------------------------------------------------------------------

def test_the_demonstrations_hold_with_no_error_of_any_kind():
    result = EZ.evaluate(cases("ezafe.json"))
    assert result["errors"] == dict.fromkeys(EZ.ERRORS, 0), result["failures"]


def test_the_held_out_cases_hold_with_no_error_of_any_kind():
    result = EZ.evaluate(cases("ezafe_heldout.json"))
    assert result["errors"] == dict.fromkeys(EZ.ERRORS, 0), result["failures"]


def test_the_case_files_have_the_eight_demonstrations_positive_negative_and_ambiguous_cases_and_are_separate():
    demo, held = cases("ezafe.json"), cases("ezafe_heldout.json")
    assert {c["name"].split(" ")[0] for c in demo} >= {"1", "2", "3", "4", "5", "6", "7", "8a", "8b"}
    for found in (demo, held):
        assert {"positive", "negative", "ambiguous"} <= {c["kind"] for c in found}
    assert not {c["name"] for c in demo} & {c["name"] for c in held}
    assert not {c["text"] for c in demo} & {c["text"] for c in held} - {"کتاب را خواند.", "ماه کامل بود."}


def test_each_kind_of_error_is_counted_apart():
    wrong = [{"name": "x", "text": "ماه کامل بود.", "readings": ["māh-e", "kāmel", "bud"], "expect": {"0": {"type": "YE", "mark": "ماهٔ"}, "1": {"type": "E"}}},
             {"name": "y", "text": "ماه کامل بود.", "readings": ["māh-e", "kāmel", "bud"], "expect": {"0": {"type": "NONE"}}},
             {"name": "z", "text": "مادر بزرگ آمد.", "readings": ["mādar-e", "bozorg", "āmad"], "expect": {"0": {"type": "E"}}}]
    errors = EZ.evaluate(wrong)["errors"]
    assert (errors["phonology"], errors["intervention"], errors["false_negative"], errors["false_positive"], errors["uncertainty"]) == (1, 1, 1, 1, 1)


def test_the_type_follows_the_spoken_final_sound_not_the_last_letter():
    heh_h, heh_e = analyze("ماه کامل بود", ["māh-e", "kāmel", "bud"])[0], analyze("بسته پستی رسید", ["baste-ye", "posti", "resid"])[0]
    assert (heh_h.ezafe_type, heh_h.final_phoneme) == ("E", "h") and (heh_e.ezafe_type, heh_e.final_phoneme) == ("YE", "e")
    assert analyze("چای داغ", ["chāy-e", "dāgh"])[0].ezafe_type == "E"
    assert analyze("صندلی من", ["sandali-ye", "man"])[0].ezafe_type == "YE"


def test_two_adjacent_words_are_no_reason_for_an_ezafe():
    found = analyze("او به خانه رفت", ["u", "be", "khāne", "raft"])
    assert [b.ezafe_type for b in found] == ["NONE"] * 4
    assert analyze("کتاب را خواند", ["ketāb-e", "rā", "khānd"])[0].ezafe_type == "UNCERTAIN", "a rule against the reading is a conflict, not a guess"


def test_an_agent_that_cannot_decide_keeps_the_boundary_uncertain_and_a_disagreement_is_a_conflict():
    unsure = analyze("مرد جوان آمد", ["mard-e", "javān", "āmad"], {0: [EZ.Opinion("editor", None, "noun_adjective", 0.4)]})[0]
    assert unsure.ezafe_type == "UNCERTAIN" and unsure.ezafe_required is None
    split = analyze("مرد جوان آمد", ["mard-e", "javān", "āmad"], {0: [EZ.Opinion("editor", True, "noun_adjective", 0.9), EZ.Opinion("checker", False, "none", 0.8)]})[0]
    assert (split.ezafe_type, split.evidence_type, split.conflicts) == ("E", "conflict", ("checker",))


def test_the_structured_output_leaves_the_intervention_undecided():
    out = analyze("بسته پستی رسید", ["baste-ye", "posti", "resid"])[0].describe()
    assert {"word", "next_word", "ezafe_required", "ezafe_type", "final_phoneme", "expected_reading", "grammatical_relation", "confidence", "evidence_type",
            "requires_tts_intervention"} <= set(out)
    assert out["expected_reading"] == "baste-ye posti" and out["requires_tts_intervention"] is None


# --- the spelling of an ezafe (bug: a hamza was written on every final ه, so dādgāh-e read as dādgāye) ------------------------------------

def test_an_ezafe_is_written_by_the_spoken_final_sound():
    assert persian("dādgāh-e") == "دادگاهِ"
    assert persian("khāne-ye").endswith("هٔ")
    assert persian("sandali-ye").endswith("لیِ")
    assert persian("sedā-ye").endswith("دای")
    assert persian("dar-e") == "دَرِ"


# --- the reading agents ----------------------------------------------------------------------------------------------------------------------

def test_the_editors_judgement_is_kept_with_its_reading_and_an_old_cache_without_one_still_loads():
    words = tokenize("مرد جوان آمد")
    raw = {"words": [{"n": 0, "latin": "mard-e", "ezafe": {"present": True, "relation": "noun_adjective", "confidence": 0.9}},
                     {"n": 1, "latin": "javān"}, {"n": 2, "latin": "āmad"}]}
    out = parse(raw, words)
    assert out[0].ezafe == {"editor": {"present": True, "relation": "noun_adjective", "confidence": 0.9}} and out[1].ezafe is None
    assert ReadingWord(**{"n": 0, "latin": "mard", "pause_after": False, "why": "", "confidence": "high", "question": ""}).ezafe is None


def test_a_checker_that_disagrees_about_an_ezafe_sends_the_word_to_the_arbiter_and_every_judgement_is_kept():
    checker = Script({"words": [], "ezafe": [{"n": 1, "present": False, "relation": "none", "confidence": 0.9}]})
    arbiter = Script({"decision": "checker", "latin": "beham", "ezafe": {"present": False, "relation": "none", "confidence": 0.95}})
    table = FilmTable.build({"people": [], "places": [], "foreign": []})
    editor = [ReadingWord(0, "sārā"), ReadingWord(1, "beham-e", ezafe={"editor": {"present": True, "relation": "noun_noun", "confidence": 0.7}}), ReadingWord(2, "goft")]
    out, doubts = asyncio.run(Checker(None, 1, table, checker, arbiter).check(item(), editor))
    assert out[1].latin == "beham" and doubts == []
    assert arbiter.seen[0]["checker"]["latin"] == "beham" and "ezafe" in arbiter.seen[0]["checker"]["proof"]
    assert set(out[1].ezafe) == {"editor", "checker", "arbiter"}
    opinions = RS.opinions_of({"B1": out})["B1"][1]
    assert {o.source for o in opinions} == {"editor", "checker", "arbiter"}


def test_a_checker_that_agrees_is_not_listed_and_calls_no_arbiter():
    arbiter = Script({"decision": "editor"})
    table = FilmTable.build({"people": [], "places": [], "foreign": []})
    out, _ = asyncio.run(Checker(None, 1, table, Script({"words": []}), arbiter).check(item(), [ReadingWord(0, "sārā"), ReadingWord(1, "beham"), ReadingWord(2, "goft")]))
    assert arbiter.seen == [] and [r.latin for r in out] == ["sārā", "beham", "goft"]


def test_the_owners_spoken_forms_and_the_ordinary_words_of_a_long_name_are_not_names_of_the_film():
    table = FilmTable.build({"people": [{"id": "s", "first": {"fa": "سارا", "latin": "sārā"}, "titles": [{"fa": "رئیس", "latin": "ra'is"}]}], "foreign": [],
                            "places": [{"fa": "دادگاه عمومیِ شهرستان میامی", "latin": "dādgāh-e omumi-ye shahrestān-e miāmi"}, {"fa": "اوهایو", "latin": "ohāyo"}]},
                           [{"surface": "سلاح", "latin": "selāh", "kind": "spoken_form"}])
    assert RS.names_of(table) == frozenset({key_of("سارا"), key_of("اوهایو")})


# --- risk and planner ------------------------------------------------------------------------------------------------------------------------

def risk_of(db, lang, text, readings, n, opinions=None):
    words = tokenize(text)
    signals = [WordSignal()] * len(words)
    bounds = EZ.EzafeAnalyzer().analyze(words, readings, opinions)
    return RiskEngine(db, lang, VOICE, MODEL).assess(words, readings, signals, bounds)[n]


def test_a_settled_ezafe_is_no_risk_and_only_an_unwritten_uncertain_or_disputed_one_adds_any(db_session, language):  # noqa: F811
    written = risk_of(db_session, language, "خانهٔ ما", ["khāne-ye", "mā"], 0)
    unwritten = risk_of(db_session, language, "خانه ما", ["khāne-ye", "mā"], 0)
    unsure = risk_of(db_session, language, "مرد جوان آمد", ["mard-e", "javān", "āmad"], 0, {0: [EZ.Opinion("editor", None, "noun_adjective", 0.3)]})
    disputed = risk_of(db_session, language, "مرد جوان آمد", ["mard-e", "javān", "āmad"], 0,
                       {0: [EZ.Opinion("editor", True, "noun_adjective", 0.9), EZ.Opinion("checker", False, "none", 0.9)]})
    assert written.signals == [] and written.level == "LOW"
    assert [s.name for s in unwritten.signals] == ["ezafe_unwritten"] and unwritten.level == "LOW", "an ezafe is not HIGH by itself"
    assert "ezafe_uncertain" in {s.name for s in unsure.signals}
    assert {"ezafe_conflict", "ezafe_unwritten"} <= {s.name for s in disputed.signals} and disputed.level == "HIGH"


def test_the_planners_own_untried_candidate_is_not_an_earlier_failure_but_a_legacy_correction_is(db_session, language):  # noqa: F811
    entry_for(db_session, language, "زندان").source = "agent"
    db_session.commit()
    assert risk_of(db_session, language, "زندان بود", ["zendān", "bud"], 0).signals == []
    entry_for(db_session, language, "زمین", status="legacy_unverified")
    assert [s.name for s in risk_of(db_session, language, "زمین بود", ["zamin", "bud"], 0).signals] == ["earlier_failure"]


def plan_of(db, lang, text, readings, signals, opinions=None):
    voicing = Voicing({}, {"b": readings}, {"b": signals}, opinions={"b": opinions or {}})
    return Planner(db, lang, VOICE, MODEL).plan("b", text, voicing)


def test_an_ezafe_that_is_the_risk_gets_only_its_ezafe_written_and_the_rest_stays(db_session, language):  # noqa: F811
    resolved = {0: [EZ.Opinion("editor", True, "noun_adjective", 0.9), EZ.Opinion("checker", False, "none", 0.9), EZ.Opinion("arbiter", True, "noun_adjective", 0.9)]}
    plan = plan_of(db_session, language, "کوچه باریک تاریک بود", ["kuche-ye", "bārik", "tārik", "bud"], [WordSignal()] * 4, resolved)
    d = plan.decisions[0]
    assert (d.strategy, d.replacement) == ("ezafe_mark", "کوچهٔ") and plan.text == "کوچهٔ باریک تاریک بود"
    assert plan.boundaries[0].requires_tts_intervention is True and plan.describe()["words"][0]["ezafe"]["ezafe_type"] == "YE"


def test_a_disputed_ezafe_no_arbiter_settled_is_escalated_and_not_written(db_session, language):  # noqa: F811
    split = {0: [EZ.Opinion("editor", True, "noun_adjective", 0.9), EZ.Opinion("checker", False, "none", 0.9)]}
    plan = plan_of(db_session, language, "کوچه باریک تاریک بود", ["kuche-ye", "bārik", "tārik", "bud"], [WordSignal()] * 4, split)
    assert plan.decisions[0].strategy == "escalate" and plan.text == "کوچه باریک تاریک بود"


def test_a_risky_word_whose_vowels_are_the_risk_gets_its_marks_with_the_ezafe_its_sound_takes(db_session, language):  # noqa: F811
    plan = plan_of(db_session, language, "دادگاه شهر بسته بود", ["dādgāh-e", "shahr", "baste", "bud"], [WordSignal("low", True, False)] + [WordSignal()] * 3)
    assert plan.decisions[0].replacement == "دادگاهِ", "a consonant h takes a kasra, never a hamza"


def test_an_ezafe_correction_is_kept_with_its_context_and_reused_only_where_the_context_matches(db_session, language):  # noqa: F811
    resolved = {0: [EZ.Opinion("editor", True, "noun_adjective", 0.9), EZ.Opinion("checker", False, "none", 0.9), EZ.Opinion("arbiter", True, "noun_adjective", 0.9)]}
    planner = Planner(db_session, language, VOICE, MODEL)
    plan = planner.plan("b", "کوچه باریک تاریک بود", Voicing({}, {"b": ["kuche-ye", "bārik", "tārik", "bud"]}, {"b": [WordSignal()] * 4}, opinions={"b": resolved}))
    planner.register(plan)
    entry = db_session.get(PronunciationEntry, db_session.get(PronunciationIntervention, plan.decisions[0].intervention_id).entry_id)
    assert (entry.sense, entry.next_word, entry.status) == ("ezafe:YE", key_of("باریک"), "candidate")
    assert json.loads(entry.grammar)["ezafe"]["grammatical_relation"] == "noun_adjective"
    entry.status, entry.approved_pronunciation = "human_approved", "کوچهٔ"
    intervention = db_session.get(PronunciationIntervention, plan.decisions[0].intervention_id)
    intervention.status = "validated"
    db_session.commit()
    same = Planner(db_session, language, VOICE, MODEL).plan("c", "کوچه باریک بود", Voicing({}, {"c": ["kuche-ye", "bārik", "bud"]}, {"c": [WordSignal()] * 3}))
    other = Planner(db_session, language, VOICE, MODEL).plan("d", "کوچه تنگ بود", Voicing({}, {"d": ["kuche-ye", "tang", "bud"]}, {"d": [WordSignal()] * 3}))
    none = Planner(db_session, language, VOICE, MODEL).plan("e", "کوچه رو دید", Voicing({}, {"e": ["kuche", "ro", "did"]}, {"e": [WordSignal()] * 3}))
    assert same.decisions[0].validated and same.text == "کوچهٔ باریک بود"
    assert not other.decisions[0].validated and other.text == "کوچه تنگ بود", "another next word is another context"
    assert none.text == "کوچه رو دید", "no ezafe there: the ezafe correction does not apply"
    assert PronunciationMemory(db_session).find(language, "کوچه", next_word=key_of("باریک"), sense="ezafe:YE", voice_id=VOICE, model_id=MODEL)[0].entry.status == "human_approved"


# --- the renderer ----------------------------------------------------------------------------------------------------------------------------

def test_the_renderer_sends_the_ezafe_the_analyzer_and_the_planner_decided(db_session, language, tmp_path, monkeypatch):  # noqa: F811
    tts, renderer = render_with(db_session, language, tmp_path, monkeypatch, "دادگاه شهر بسته بود", ["dādgāh-e", "shahr", "baste", "bud"],
                                [WordSignal("low", True, False)] + [WordSignal()] * 3)
    assert [r.text for r in tts.requests] == ["دادگاهِ شهر بسته بود"]
    words = renderer.planner.report()["blocks"]["EN_a_01"]["words"]
    assert words[0]["ezafe"]["ezafe_type"] == "E" and words[0]["ezafe"]["requires_tts_intervention"] is True
    plain, _ = render_with(db_session, language, tmp_path, monkeypatch, "خانه ما کوچک بود", ["khāne-ye", "mā", "kuchak", "bud"], [WordSignal()] * 4)
    assert [r.text for r in plain.requests] == ["خانه ما کوچک بود"], "a correct, low-risk ezafe is not touched"


def test_the_film_table_sets_a_words_sounds_and_keeps_the_ezafe_of_the_sentence():
    from app.documentary.reading.answer import enforce

    table = FilmTable.build({"people": [], "places": [], "foreign": [{"fa": "روی", "latin": "ru"}]})
    words = tokenize("روی میز بود")
    out, notes = enforce([ReadingWord(0, "ru-ye"), ReadingWord(1, "miz"), ReadingWord(2, "bud")], words, table, True)
    assert out[0].latin == "ru-ye" and notes == []
    out, notes = enforce([ReadingWord(0, "rui-ye"), ReadingWord(1, "miz"), ReadingWord(2, "bud")], words, table, True)
    assert out[0].latin == "ru-ye" and len(notes) == 1


def test_a_reading_cache_whose_table_dropped_a_written_ezafe_is_read_by_the_texts_writing():
    words = tokenize("روی صفحه بود")
    kept = EZ.EzafeAnalyzer().analyze(words, ["ru", "safhe", "bud"], table_set=frozenset({0}))[0]
    assert (kept.ezafe_type, kept.evidence_type) == ("YE", "orthography")
    agent = EZ.EzafeAnalyzer().analyze(words, ["ru", "safhe", "bud"])[0]
    assert agent.ezafe_type == "UNCERTAIN", "an agent's reading that drops a written ezafe is a conflict"


def test_an_ezafe_before_the_pronoun_ham_is_said():
    assert analyze("کنار هم نشستن", ["kenār-e", "ham", "neshastan"])[0].ezafe_type == "E"


# --- a final long i (EZAFE_FINAL_I) ----------------------------------------------------------------------------------------------------------

RESOLVED = {0: [EZ.Opinion("editor", True, "noun_possessor", 0.9), EZ.Opinion("checker", False, "none", 0.9), EZ.Opinion("arbiter", True, "noun_possessor", 0.9)]}


def test_a_final_long_i_is_its_own_class_with_the_connected_forms_a_listener_may_accept():
    b = analyze("بینایی انسان ضعیف شد", ["bināyi-ye", "ensān", "za'if", "shod"])[0]
    assert (b.ezafe_type, b.phonological_class, b.final_phoneme) == ("YE", "EZAFE_FINAL_I", "i")
    assert b.expected_reading == "bināyi-ye ensān" and b.realizations == ("bināyi-ye ensān", "bināyi-e ensān")
    assert analyze("صدایی بلند شنید", ["sedāyi", "boland", "shenid"])[0].ezafe_type == "NONE", "the indefinite -i: the reading decides, none is added"


def test_the_ezafe_of_a_final_long_i_is_a_kasra_and_never_another_yeh():
    for reading, word in (("bināyi-ye", "بیناییِ"), ("zibāyi-ye", "زیباییِ"), ("sandali-ye", "لیِ")):
        assert persian(reading).endswith(word) and not persian(reading).endswith("ییی")
    words = tokenize("زیبایی طبیعت")
    assert EZ.mark(words[0], analyze("زیبایی طبیعت", ["zibāyi-ye", "tabi'at"])[0]) == "زیباییِ"


def test_a_final_long_i_is_not_a_risk_by_itself(db_session, language):  # noqa: F811
    risk = risk_of(db_session, language, "بینایی انسان ضعیف شد", ["bināyi-ye", "ensān", "za'if", "shod"], 0)
    assert [s.name for s in risk.signals] == ["ezafe_unwritten"] and risk.level == "LOW"


def test_the_reference_and_the_text_the_voice_is_given_are_kept_apart(db_session, language):  # noqa: F811
    plan = plan_of(db_session, language, "بینایی انسان ضعیف شد", ["bināyi-ye", "ensān", "za'if", "shod"], [WordSignal()] * 4, RESOLVED)
    b = plan.boundaries[0]
    assert plan.decisions[0].strategy == "ezafe_mark" and plan.text == "بیناییِ انسان ضعیف شد"
    assert (b.expected_reading, b.tts_form, b.requires_tts_intervention) == ("bināyi-ye ensān", "بیناییِ", True)


def test_a_final_i_correction_counts_only_after_the_voice_is_verified(db_session, language):  # noqa: F811
    planner = Planner(db_session, language, VOICE, MODEL)
    plan = planner.plan("b", "بینایی انسان ضعیف شد", Voicing({}, {"b": ["bināyi-ye", "ensān", "za'if", "shod"]}, {"b": [WordSignal()] * 4}, opinions={"b": RESOLVED}))
    planner.register(plan)
    planner.learn("b", "بینایی", heard_right=True)
    intervention = db_session.get(PronunciationIntervention, plan.decisions[0].intervention_id)
    entry = db_session.get(PronunciationEntry, intervention.entry_id)
    assert (intervention.status, intervention.successes, entry.status) == ("candidate", 0, "candidate"), "a listening model's 'right' validates nothing"
    assert json.loads(entry.grammar)["ezafe"]["phonological_class"] == "EZAFE_FINAL_I"
    again = Planner(db_session, language, VOICE, MODEL).plan("c", "بینایی انسان ضعیف شد", Voicing({}, {"c": ["bināyi-ye", "ensān", "za'if", "shod"]}, {"c": [WordSignal()] * 4}))
    assert not again.decisions[0].validated and again.text == "بینایی انسان ضعیف شد", "an unverified correction is not reused"


def test_the_renderer_sends_the_kasra_of_a_final_long_i_when_the_planner_decides_it(db_session, language, tmp_path, monkeypatch):  # noqa: F811
    tts, _ = render_with(db_session, language, tmp_path, monkeypatch, "زیبایی طبیعت دیدنی بود", ["zibāyi-ye", "tabi'at", "didani", "bud"],
                         [WordSignal("low", True, False)] + [WordSignal()] * 3)
    assert [r.text for r in tts.requests] == ["زیباییِ طبیعت دیدنی بود"]

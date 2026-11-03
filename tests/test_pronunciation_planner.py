"""The pronunciation risk engine and the adaptive TTS planner: the decision specifications (examples and held-out cases), what reaches the voice, the
memory's context rules, the naturalness budget, and that no failure is ever an approval."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from test_voice_render import FakeASR, FakeTTS, _block, _render

from app.core.ai_config import ai_config
from app.db.models import (
    PhoneticSample,
    PronunciationAuditLog,
    PronunciationEntry,
    PronunciationEntryVersion,
    PronunciationEvidence,
    PronunciationIntervention,
)
from app.documentary.pronunciation_memory import PronunciationMemory
from app.documentary.pronunciation_planner import Planner, naturalness
from app.documentary.pronunciation_risk import RiskEngine
from app.documentary.reading.voicing import Voicing, WordSignal
from app.documentary.reading.words import key_of, tokenize
from app.documentary.voice_render import VoiceRenderer

CASES = Path(__file__).resolve().parents[1] / "prompts" / "pronunciation" / "decision_cases"
VOICE, MODEL = "v1", "m1"


def load(name: str) -> list[dict]:
    return json.loads((CASES / name).read_text(encoding="utf-8"))["cases"]


@pytest.fixture
def language(db_session):
    lang = f"fa-t{uuid.uuid4().hex[:8]}"
    yield lang
    ids = [e.id for e in db_session.query(PronunciationEntry).filter_by(language=lang)]
    for model in (PronunciationEvidence, PronunciationIntervention, PronunciationAuditLog, PronunciationEntryVersion):
        db_session.query(model).filter(model.entry_id.in_(ids)).delete(synchronize_session=False)
    db_session.query(PronunciationEntry).filter_by(language=lang).delete(synchronize_session=False)
    db_session.query(PhoneticSample).filter_by(language=lang).delete(synchronize_session=False)
    db_session.commit()


def entry_for(db, language, surface, **fields) -> PronunciationEntry:
    fields = {"status": "candidate", "expected_irab": surface, **fields}
    entry = PronunciationEntry(language=language, surface=surface, surface_key=key_of(surface), source="test", version=1, scope="context", **fields)
    db.add(entry)
    db.commit()
    return entry


def build(db, language, case) -> tuple[Voicing, str, dict]:
    """The block of a case, with the memory rows its setup asks for."""
    words, setup = tokenize(case["text"]), case["setup"]
    target = words[case["target"]]
    signals = [WordSignal(**case["signal"]) if w.n == case["target"] else WordSignal() for w in words]
    if "entry" in setup or "intervention" in setup or "intervention_failures" in setup or "evidence" in setup:
        spec = dict(setup.get("entry") or {"status": "human_approved", "expected_irab": target.surface})
        surface = spec.pop("surface", target.surface)
        entry = entry_for(db, language, surface, **spec)
        if "intervention" in setup:
            spec_i = setup["intervention"]
            row = PronunciationMemory(db).add_intervention(entry.id, kind=spec_i["kind"], exact_form=surface, replacement=spec_i["replacement"])
            row.status = spec_i["status"]
            db.commit()
        if setup.get("intervention_failures"):
            row = PronunciationMemory(db).add_intervention(entry.id, kind="irab", exact_form=surface, replacement=entry.expected_irab)
            row.failures = setup["intervention_failures"]
            db.commit()
        if "evidence" in setup:
            PronunciationMemory(db).add_evidence(surface=surface, entry_id=entry.id, **setup["evidence"])
    if "label" in setup:
        db.add(PhoneticSample(language=language, source="recording", surface=target.surface, sentence=case["text"], audio_path=f"uploads/{language}.wav", category="other",
                              candidates="[]", status="human_verified", **setup["label"]))
        db.commit()
    names = frozenset({target.key}) if case["names"] else frozenset()
    return Voicing({}, {"b": case["readings"]}, {"b": signals}, names), case["text"], setup


def run_risk(db, language, case):
    voicing, text, _ = build(db, language, case)
    words = tokenize(text)
    risks = RiskEngine(db, language, VOICE, MODEL, voicing.names).assess(words, case["readings"], voicing.signals["b"])
    return risks[case["target"]]


@pytest.mark.parametrize("name", ["risk.json", "risk_heldout.json"])
def test_the_risk_specification_cases_hold(db_session, language, name):
    for case in load(name):
        lang = f"{language}-{uuid.uuid4().hex[:4]}"
        risk = run_risk(db_session, lang, case)
        want = case["expect"]
        assert risk.level == want["level"], (case["name"], risk.level, risk.score, risk.describe())
        assert sorted(s.name for s in risk.signals) == sorted(want["signals"]), (case["name"], risk.describe())
        if "verification" in want:
            assert risk.verification == want["verification"]
        if risk.level in ("HIGH", "CRITICAL"):
            assert risk.rationale, case["name"]                  # a HIGH or CRITICAL word always says why
        _clean(db_session, lang)


def _clean(db, lang):
    ids = [e.id for e in db.query(PronunciationEntry).filter_by(language=lang)]
    for model in (PronunciationEvidence, PronunciationIntervention, PronunciationAuditLog, PronunciationEntryVersion):
        db.query(model).filter(model.entry_id.in_(ids)).delete(synchronize_session=False)
    db.query(PronunciationEntry).filter_by(language=lang).delete(synchronize_session=False)
    db.query(PhoneticSample).filter_by(language=lang).delete(synchronize_session=False)
    db.commit()


def run_plan(db, lang, case):
    voicing, text, setup = build(db, lang, case)
    return Planner(db, lang, VOICE, MODEL).plan("b", text, voicing, allow_dictionary=setup.get("allow_dictionary", False))


@pytest.mark.parametrize("name", ["planner.json", "planner_heldout.json"])
def test_the_planner_specification_cases_hold(db_session, language, name):
    for case in load(name):
        lang = f"{language}-{uuid.uuid4().hex[:4]}"
        plan = run_plan(db_session, lang, case)
        want, decision = case["expect"], plan.decisions[case["target"]]
        assert decision.strategy == want["strategy"], (case["name"], decision.describe())
        if "text" in want:
            assert plan.text == want["text"], case["name"]
        if "rules" in want:
            assert len(plan.rules) == want["rules"]
        if "needs_review" in want:
            assert decision.needs_review is want["needs_review"]
        if "changed_words" in want:
            assert [d.n for d in plan.decisions if d.strategy in ("selective_marks", "respelling")] == want["changed_words"]
        _clean(db_session, lang)


def test_the_example_files_cover_every_required_kind_with_at_least_eight_cases_and_are_separate_from_the_held_out_ones():
    kinds = {"no_intervention", "ambiguous_short_vowel", "ezafe", "colloquial", "proper_name", "historical_error", "context_dependent", "conflicting_evidence"}
    for name in ("risk.json", "planner.json"):
        cases = load(name)
        assert len(cases) >= 8 and kinds <= {c["category"] for c in cases}, name
    for trained, held in (("risk.json", "risk_heldout.json"), ("planner.json", "planner_heldout.json")):
        assert not {c["name"] for c in load(trained)} & {c["name"] for c in load(held)}


def test_a_low_risk_word_is_not_a_verified_word(db_session, language):
    risk = run_risk(db_session, language, load("risk.json")[0])
    assert risk.level == "LOW" and risk.verification == "unverified"


def test_a_sentence_of_low_risk_words_reaches_the_voice_exactly_as_written(db_session, language):
    case = {"text": "این کتاب بود و او رفت", "readings": ["in", "ketāb", "bud", "va", "u", "raft"], "target": 1, "signal": {}, "names": False, "setup": {}}
    assert run_plan(db_session, language, case).text == case["text"]


def test_full_marks_are_never_applied_to_a_whole_sentence_even_when_every_word_is_risky(db_session, language):
    words = "این کتاب را من دیدم و او گفت که رفت"
    readings = ["in", "ketāb", "rā", "man", "didam", "va", "u", "goft", "ke", "raft"]
    voicing = Voicing({}, {"b": readings}, {"b": [WordSignal("low", True, False)] * len(readings)})
    plan = Planner(db_session, language, VOICE, MODEL).plan("b", words, voicing)
    limit = max(1, int(ai_config.pronunciation_risk.max_changed_fraction * len(readings)))
    assert 0 < plan.naturalness["unvalidated_changed"] <= limit and plan.naturalness["longest_run"] <= ai_config.pronunciation_risk.max_changed_in_a_row
    assert plan.text != words and len(plan.text.split()) == len(words.split())


def test_the_naturalness_measure_counts_changed_words_and_runs():
    from app.documentary.pronunciation_planner import Decision

    ds = [Decision(i, "w", "HIGH", s, "") for i, s in enumerate(["keep", "respelling", "selective_marks", "keep", "selective_marks"])]
    assert naturalness(ds) == {"words": 5, "changed": 3, "changed_fraction": 0.6, "longest_run": 2, "unvalidated_changed": 3}


def test_a_final_vowel_e_keeps_its_letter_and_only_a_reading_with_a_consonant_h_gets_the_other_one():
    from app.documentary.reading.spelling import persian

    assert persian("khāne") == "خانه" and persian("mosallah").endswith("ه") and persian("mosallah", final_h=True).endswith("ح")


def test_the_approved_pronunciation_is_reused_only_where_the_context_matches(db_session, language):
    context = [c for c in load("planner.json") if c["category"] == "context_dependent"]
    plans = [run_plan(db_session, f"{language}-{i}", c).decisions[c["target"]].strategy for i, c in enumerate(context)]
    assert plans == ["keep", "respelling", "keep"]
    for i in range(len(context)):
        _clean(db_session, f"{language}-{i}")


def test_what_a_person_approved_for_a_word_still_goes_to_the_voice_as_before(db_session, language):
    voicing = Voicing({key_of("عهد"): "'ahd"}, {"b": ["az", "'ahd", "harf", "zad"]})
    assert Planner(db_session, language, VOICE, MODEL).plan("b", "از عهد حرف زد", voicing).text == "از اَهد حرف زد"


def test_a_block_whose_words_are_not_the_words_of_its_reading_is_left_as_written(db_session, language):
    plan = Planner(db_session, language, VOICE, MODEL).plan("b", "از عهد حرف زد", Voicing({}, {"b": ["az"]}))
    assert plan.text == "از عهد حرف زد" and "left as written" in plan.note


def test_failed_attempts_never_approve_anything_and_raise_the_next_risk(db_session, language):
    case = load("planner.json")[1]                                   # a high-risk word with a candidate change
    voicing, text, _ = build(db_session, language, case)
    planner = Planner(db_session, language, VOICE, MODEL)
    plan = planner.plan("b", text, voicing)
    planner.register(plan)
    before = plan.risks[case["target"]].score
    for _ in range(3):
        planner.learn("b", "کتاب", heard_right=False)
    statuses = {e.status for e in db_session.query(PronunciationEntry).filter_by(language=language)}
    assert statuses and not statuses & {"human_approved", "auto_verified"}
    assert {i.status for i in db_session.query(PronunciationIntervention).join(PronunciationEntry, PronunciationEntry.id == PronunciationIntervention.entry_id)
            .filter(PronunciationEntry.language == language)} == {"failed"}
    again = Planner(db_session, language, VOICE, MODEL).plan("b", text, voicing)
    assert again.risks[case["target"]].score > before


def test_a_listening_models_success_is_evidence_only_and_validates_nothing_by_default(db_session, language):
    case = load("planner.json")[1]
    voicing, text, _ = build(db_session, language, case)
    planner = Planner(db_session, language, VOICE, MODEL)
    planner.register(planner.plan("b", text, voicing))
    planner.learn("b", "کتاب", heard_right=True)
    assert {i.status for i in db_session.query(PronunciationIntervention).join(PronunciationEntry, PronunciationEntry.id == PronunciationIntervention.entry_id)
            .filter(PronunciationEntry.language == language)} == {"candidate"}
    assert db_session.query(PronunciationEvidence).join(PronunciationEntry, PronunciationEntry.id == PronunciationEvidence.entry_id).filter(
        PronunciationEntry.language == language, PronunciationEvidence.verdict == "match").count() == 1


def test_a_candidate_never_overwrites_an_entry_a_person_decided_about(db_session, language):
    entry_for(db_session, language, "کتاب", status="human_approved", approved_pronunciation="کُتاب", expected_irab="کُتاب")
    case = load("planner.json")[1]
    voicing, text, _ = build(db_session, language, {**case, "setup": {}})
    planner = Planner(db_session, language, VOICE, MODEL)
    plan = planner.plan("b", text, voicing)
    assert plan.decisions[1].strategy == "respelling" and plan.text == "این کُتاب بود"
    kept = db_session.query(PronunciationEntry).filter_by(language=language).one()
    assert (kept.status, kept.approved_pronunciation) == ("human_approved", "کُتاب")


# --- what reaches the voice -----------------------------------------------------------------------------------------------------------------

def render_with(db, language, tmp_path, monkeypatch, text, readings, signals):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    tts = FakeTTS()
    renderer = VoiceRenderer(provider=tts, asr=FakeASR())
    renderer.voicing = Voicing({}, {"EN_a_01": readings}, {"EN_a_01": signals})
    renderer.planner = Planner(db, language, VOICE, MODEL)
    _render(renderer, [_block("EN_a_01", "act1", text)])
    return tts, renderer


def test_the_risk_of_a_word_decides_the_text_the_voice_is_sent(db_session, language, tmp_path, monkeypatch):
    low, _ = render_with(db_session, language, tmp_path, monkeypatch, "این کتاب بود", ["in", "ketāb", "bud"], [WordSignal()] * 3)
    high, renderer = render_with(db_session, language, tmp_path, monkeypatch, "این کتاب بود", ["in", "ketāb", "bud"], [WordSignal(), WordSignal("low", True, False), WordSignal()])
    assert [r.text for r in low.requests] == ["این کتاب بود"]
    assert [r.text for r in high.requests] == ["این کِتاب بود"]
    assert renderer.planner.plans["EN_a_01"].decisions[1].strategy == "selective_marks"


def test_the_plan_of_a_render_is_kept_with_the_narration(db_session, language, tmp_path, monkeypatch):
    _, renderer = render_with(db_session, language, tmp_path, monkeypatch, "از عهد حرف زد", ["az", "'ahd", "harf", "zad"], [WordSignal(), WordSignal("low", True, False), WordSignal(), WordSignal()])
    report = renderer.planner.report()
    assert report["escalations"][0]["surface"] == "عهد" and report["blocks"]["EN_a_01"]["words"][1]["risk"]["level"] == "CRITICAL"
    assert report["blocks"]["EN_a_01"]["words"][1]["risk"]["signals"]


def test_a_risky_word_the_dictionary_already_has_a_rule_for_is_not_changed_on_top_of_it(db_session, language):
    voicing = Voicing({}, {"b": ["in", "ketāb", "bud"]}, {"b": [WordSignal(), WordSignal("low", True, False), WordSignal()]})
    plan = Planner(db_session, language, VOICE, MODEL).plan("b", "این کتاب بود", voicing, covered=frozenset({key_of("کتاب")}))
    assert plan.text == "این کتاب بود" and plan.decisions[1].level == "HIGH" and "already has a rule" in plan.decisions[1].basis


def test_the_escalations_list_each_word_once_with_its_blocks(db_session, language):
    planner = Planner(db_session, language, VOICE, MODEL)
    voicing = Voicing({}, {"a": ["az", "'ahd"], "b": ["az", "'ahd"]}, {k: [WordSignal(), WordSignal("low", True, False)] for k in "ab"})
    for block in "ab":
        planner.plan(block, "از عهد", voicing)
    assert [(e["surface"], e["blocks"]) for e in planner.escalations()] == [("عهد", ["a", "b"])]


def test_the_reading_loop_reports_what_it_heard_to_the_planner_and_a_fix_is_not_an_approval(db_session, language, monkeypatch):
    from app.documentary.reading.inspector import Finding
    from app.documentary.reading.loop import ReadingLoop

    heard = []
    loop = ReadingLoop(db_session, 0, language, [], None, Voicing({}, {}), on_result=lambda block, surface, right: heard.append((block, surface, right)))
    monkeypatch.setattr(loop, "keep", lambda *a, **k: None)
    finding = Finding("b", 1, "کتاب", "ketāb", "ketob", "ketob", 2.0)
    loop.settle({("b", 1): finding}, {})
    assert heard == [("b", "کتاب", True)]
    assert db_session.query(PronunciationEntry).filter_by(language=language).count() == 0

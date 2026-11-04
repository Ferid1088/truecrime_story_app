"""The phoneme-first reference: the seed and held-out REFERENCE_IRAB cases (every metric apart), the validator against broken IRABs, phonemes,
syllables, stress, morphology, the lossless offsets of the words, the homograph signal, the planner writing only a validated reference, the memory's
trust levels and the explanation of a plan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_pronunciation_planner import MODEL, VOICE, entry_for, language  # noqa: F401  (language is a fixture)

from app.db.models import PronunciationEntry, PronunciationIntervention
from app.documentary import pronunciation_explain as PX
from app.documentary.pronunciation_memory import trust_level
from app.documentary.pronunciation_planner import Planner
from app.documentary.pronunciation_risk import RiskEngine
from app.documentary.reading import reference as REF
from app.documentary.reading.voicing import Voicing, WordSignal
from app.documentary.reading.words import tokenize

CASES = Path(__file__).resolve().parents[1] / "prompts" / "pronunciation" / "decision_cases"


def cases(name: str) -> list[dict]:
    return json.loads((CASES / name).read_text(encoding="utf-8"))["cases"]


def ref(surface: str, reading: str) -> REF.Reference:
    return REF.reference_of(tokenize(surface)[0], reading)


@pytest.mark.parametrize("name", ["reference.json", "reference_heldout.json"])
def test_the_reference_cases_hold_on_every_metric(name):
    r = REF.evaluate(cases(name))
    assert r["failures"] == []
    assert (r["irab_exact"], r["phonemes_right"], r["valid"]) == (r["cases"],) * 3 and r["sukun_right"] == r["sukun_letters"]


def test_the_case_files_say_they_are_not_reviewed_and_are_separate():
    for name in ("reference.json", "reference_heldout.json"):
        assert json.loads((CASES / name).read_text(encoding="utf-8"))["reviewed_by"] is None
    assert not {c["name"] for c in cases("reference.json")} & {c["name"] for c in cases("reference_heldout.json")}


def test_the_mandatory_examples():
    assert ref("بهم", "beham").irab == "بِهَمْ"
    assert (ref("عمر", "omr").irab, ref("عمر", "omr-e").irab) == ("عُمْرْ", "عُمْرِ"), "the ezafe replaces the final sukun"
    assert (ref("دادگاه", "dādgāh").irab, ref("دادگاه", "dādgāh-e").irab) == ("دادْگاهْ", "دادْگاهِ")
    assert (ref("مرد", "mard").irab, ref("مرد", "mord").irab) == ("مَرْدْ", "مُرْدْ")
    assert ref("شنوایی", "shenavāyi-ye").irab == "شِنَواییِ" and ref("بوی", "bu-ye").irab == "بویِ"
    assert ref("نمی‌خوام", "nemikhām").irab == "نِمی‌خوامْ"
    assert (ref("حق", "hagh").irab, ref("حق", "haghgh").irab) == ("حَقْ", "حَقّ"), "a shadda only where the reading geminates"


def test_a_reading_the_letters_cannot_spell_has_no_reference_and_nothing_is_invented():
    r = ref("گلوله", "gulle")
    assert r.irab is None and not r.valid


@pytest.mark.parametrize("bad, reading, problem", [
    ("مَرْدْ", "mard-e", "final sukun"),              # a sukun where the ezafe is said
    ("مُرْدْ", "mard", "reads"),                       # another vowel
    ("مَرْتْ", "mard", "letters changed"),             # another letter
    ("مَرَْدْ", "mard", "two vowel marks"),            # a vowel and a sukun on one letter
])
def test_the_validator_finds_what_is_wrong_with_an_irab(bad, reading, problem):
    found = REF.validate("مرد", REF.phonemes_of(reading), bad)
    assert found and any(problem in p for p in found), found


def test_a_mark_on_a_half_space_and_a_mark_the_text_already_has_are_caught():
    assert REF.validate("می‌رود", REF.phonemes_of("miravad"), "می‌" + "َ" + "رَوَدْ") == ("a mark stands on no letter",)
    assert any("own mark" in p for p in REF.validate("بازِ", REF.phonemes_of("bāz"), "بازْ"))


def test_phonemes_syllables_stress_and_morphology():
    r = ref("خونه‌شون", "khuneshun")
    assert r.ipa == "xuːneʃuːn" and r.syllables == ("khu", "ne", "shun") and r.stress == 1, "an enclitic is not stressed"
    assert [m.kind for m in r.morphology] == ["stem", "enclitic"]
    assert ref("نمی‌خوام", "nemikhām").stress == 0, "a negation prefix takes the stress"
    assert [m.kind for m in ref("کتاب‌هاش", "ketābhāsh").morphology] == ["stem", "plural", "enclitic"]
    assert [m.kind for m in ref("کتابم", "ketābam").morphology] == ["stem"], "no boundary is guessed where the writing shows none"
    assert REF.syllables_of(REF.phonemes_of("flin"))[1] == ("an onset cluster Persian does not have: fli",), "a foreign cluster is noted, not hidden"
    assert REF.syllables_of(REF.phonemes_of("khuneash")) == (("khu", "ne", "'ash"), ("a vowel after a vowel: a glottal stop or a glide is said before ash",))


def test_every_word_keeps_its_place_in_the_original_text():
    text = "[serious] حالا می‌رسیم به بقیه‌ی حکم. «همسرم، دو"
    assert all(text[w.start:w.end] == w.surface for w in tokenize(text))


def test_a_homograph_is_a_risk_the_letters_cannot_settle(db_session, language):  # noqa: F811
    words = tokenize("او در تصادف مرد")
    risks = RiskEngine(db_session, language, VOICE, MODEL).assess(words, ["u", "dar", "tasādof", "mord"])
    assert [s.name for s in risks[3].signals] == ["homograph"]
    marked = RiskEngine(db_session, language, VOICE, MODEL).assess(tokenize("او مُرد"), ["u", "mord"])
    assert marked[1].signals == [], "a homograph the text already marks is settled"


def test_the_planner_writes_the_minimal_marking_of_a_validated_reference_and_escalates_an_invalid_one(db_session, language):  # noqa: F811
    doubt = WordSignal("low", True, False)
    plan = Planner(db_session, language, VOICE, MODEL).plan("b", "او در تصادف مرد", Voicing({}, {"b": ["u", "dar", "tasādof", "mord"]}, {"b": [WordSignal()] * 3 + [doubt]}))
    d = plan.decisions[3]
    assert (d.strategy, d.replacement, d.reference.irab) == ("selective_marks", "مُرد", "مُرْدْ"), "the voice gets the short vowels, the reference has the sukuns"
    assert plan.text == "او در تصادف مُرد" and plan.describe()["words"][3]["decision"]["reference"]["valid"] is True
    bad = Planner(db_session, language, VOICE, MODEL).plan("c", "از گلوله گفت", Voicing({}, {"c": ["az", "gulle", "goft"]}, {"c": [WordSignal(), doubt, WordSignal()]}))
    assert bad.decisions[1].strategy == "escalate" and bad.text == "از گلوله گفت"
    assert "reference_unaligned" in {s.name for s in bad.risks[1].signals}


def test_a_candidate_is_kept_with_its_reference_and_its_trust_level_is_read_not_promoted(db_session, language):  # noqa: F811
    planner = Planner(db_session, language, VOICE, MODEL)
    plan = planner.plan("b", "او در تصادف مرد", Voicing({}, {"b": ["u", "dar", "tasādof", "mord"]}, {"b": [WordSignal()] * 3 + [WordSignal("low", True, False)]}))
    planner.register(plan)
    intervention = db_session.get(PronunciationIntervention, plan.decisions[3].intervention_id)
    entry = db_session.get(PronunciationEntry, intervention.entry_id)
    assert json.loads(entry.grammar)["reference"]["reference_irab"] == "مُرْدْ"
    assert trust_level(entry, [intervention]) == "LINGUISTICALLY_VALIDATED" and entry.status == "candidate"
    approved = entry_for(db_session, language, "گل", status="human_approved")
    legacy = entry_for(db_session, language, "علم", status="legacy_unverified")
    assert (trust_level(approved, []), trust_level(legacy, [])) == ("OWNER_APPROVED_READING", "LEGACY_UNVERIFIED")


def test_the_explanation_of_a_plan_shows_why_a_word_changed_and_what_the_voice_is_given(db_session, language, monkeypatch):  # noqa: F811
    class Version:
        id, case_id = 7, 1

    voicing = Voicing({}, {"b": ["u", "dar", "tasādof", "mord"]}, {"b": [WordSignal()] * 3 + [WordSignal("low", True, False)]})
    monkeypatch.setattr(PX, "load", lambda db, v, lang: (Version(), {"blocks": [{"block_id": "b", "text": "او در تصادف مرد"}]}, voicing, frozenset()))
    monkeypatch.setattr(PX, "block_times", lambda version, lang: {"b": (1.0, 4.0)})
    out = PX.explain(db_session, 7, "fa")
    word = next(w for w in out["words"] if w["word"] == "مرد")
    assert (word["tts_input"], word["reference"]["reference_irab"], word["verification"]) == ("مُرد", "مُرْدْ", "UNVERIFIED")
    assert word["audio"] == {"start": 1.0, "end": 4.0} and word["sentence"] == "او در تصادف مرد" and out["counts"]["changed"] == 1
    assert {w["word"] for w in out["words"]} == {"مرد"}, "only the words worth explaining"


@pytest.mark.parametrize("surface, reading, kwargs, status, cause", [
    ("مرد", "mard", {}, "VALID", ""),
    ("۹۱۱", "NUM", {}, "NOT_APPLICABLE", "digits"),
    ("پرت", "pat", {"owner": True}, "UNREPRESENTABLE_BY_IRAB", "owner's spoken form"),
    ("همه‌چیز", "hamechi", {}, "UNREPRESENTABLE_BY_IRAB", "colloquial form"),
    ("دیجیتال", "digitāl", {}, "READING_UNRESOLVED", "consonant"),
    ("نشیمن", "neshimān", {}, "READING_UNRESOLVED", "long ā"),
    ("آشفته", "āshufte", {}, "READING_UNRESOLVED", "long u"),
    ("سی‌وشش", "sioshish", {}, "READING_UNRESOLVED", "long i"),
    ("زیادی", "ziyād", {}, "READING_UNRESOLVED", "final yeh"),
    ("الکترونیکی‌ای", "elektroniki", {}, "READING_UNRESOLVED", "suffix"),
    ("بازِ", "bāz", {}, "READING_UNRESOLVED", "contradicts a mark"),
    ("مرد", "mard", {"inconsistent": True}, "READING_UNRESOLVED", "otherwise elsewhere"),
    ("مرد", "mard", {"doubted": True}, "READING_UNRESOLVED", "doubt"),
])
def test_unrepresentable_and_unresolved_are_kept_apart(surface, reading, kwargs, status, cause):
    word = tokenize(surface)[0]
    args = {"owner": False, "doubted": False, "inconsistent": False, **kwargs}
    got = REF.diagnose(word, reading, REF.reference_of(word, reading), **args)
    assert got[0] == status and cause in got[1], got


def test_the_joined_number_words_and_conjunction():
    assert ref("سی‌ویک", "si-o-yek").irab == "سی‌ویِکْ"


def test_the_planners_voice_form_is_checked_against_the_reference():
    assert REF.form_fits("مُرْدْ", "مُرد") and REF.form_fits("مُرْدْ", "مُرْدْ")
    assert not REF.form_fits("مُرْدْ", "مَرد"), "a mark the reference does not have"
    assert not REF.form_fits("مُرْدْ", "مُرت"), "another letter"


def test_planning_never_changes_the_original_text(db_session, language):  # noqa: F811
    text = "او در تصادف مرد"
    original = text.encode("utf-8")
    Planner(db_session, language, VOICE, MODEL).plan("b", text, Voicing({}, {"b": ["u", "dar", "tasādof", "mord"]}, {"b": [WordSignal()] * 3 + [WordSignal("low", True, False)]}))
    assert text.encode("utf-8") == original

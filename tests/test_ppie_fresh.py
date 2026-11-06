"""The fresh PPIE path of a Persian narration: the approved spoken version is the source (never a voice-prepare rewrite), every run reads from scratch,
no dictionary, lexicon, pronunciation memory, stored reading or earlier owner decision reaches the planner or the voice, numbers are read by their
context, the Persian voice settings hold for every style, nothing the run decides is kept, and the other languages keep their own paths."""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import uuid4

import pytest
from test_pronunciation_planner import MODEL, VOICE, entry_for, language  # noqa: F401  (language is a fixture)
from test_reading_agents import item, no_provider, review_mode, stage, stage_env  # noqa: F401  (fixtures: no model is called)
from test_voice_render import FakeTTS, _block

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    PronunciationEntry,
    PronunciationEvidence,
    PronunciationIntervention,
    PronunciationLexicon,
    ReadingApproval,
    ReadingDoubt,
    StoryVersion,
)
from app.documentary import audio as A
from app.documentary import jobs as J
from app.documentary import semantic_review as SR
from app.documentary.pronunciation_planner import Planner
from app.documentary.reading import numbers as NUM
from app.documentary.reading import stage as RS
from app.documentary.reading.answer import ReadingWord
from app.documentary.reading.text import ReadingResult
from app.documentary.reading.voicing import Voicing, WordSignal
from app.documentary.reading.words import tokenize
from app.documentary.voice_render import VoiceRenderer

FILM_DICTIONARY = [{"pronunciation_dictionary_id": "LEGACY", "version_id": "V"}]
FILM_RULES = [{"string_to_replace": "عمر", "type": "alias", "alias": "اُمر"}]


@pytest.fixture
def versions(db_session):
    """A scratch case with a master, its approved spoken version and a voice-prepare rewrite of it (as Case 15 has v270 and v289), per language."""
    case = Case(canonical_title="Scratch: PPIE source", slug=f"scratch-ppie-{uuid4().hex[:8]}", status="new", language="fa", origin="scratch")
    db_session.add(case)
    db_session.flush()
    master = StoryVersion(case_id=case.id, version=1, kind="master", language="fa", story_text="m", narrative_angle="a", status="ready")
    db_session.add(master)
    db_session.flush()
    made = {}
    for lang in ("fa", "de"):
        approved = StoryVersion(case_id=case.id, version=2, kind="spoken", language=lang, story_text="عمر ۹۱۱", narrative_angle="a", status="ready",
                                master_version_id=master.id, narrative_structure=json.dumps({"blueprint_id": 1}))
        db_session.add(approved)
        db_session.flush()
        rewrite = StoryVersion(case_id=case.id, version=3, kind="spoken", language=lang, story_text="عُمر ۹۱۱", narrative_angle="a", status="ready",
                               master_version_id=master.id,
                               narrative_structure=json.dumps({"blueprint_id": 1, "prepared": {"from_version_id": approved.id, "rules": FILM_RULES}}))
        db_session.add(rewrite)
        db_session.flush()
        made[lang] = (approved, rewrite)
    db_session.commit()
    yield master, made
    db_session.query(StoryVersion).filter(StoryVersion.case_id == case.id).delete()
    db_session.query(Case).filter_by(id=case.id).delete()
    db_session.commit()


def test_the_approved_spoken_version_is_the_source_and_a_voice_prepare_rewrite_never_is(db_session, versions):
    master, made = versions
    assert J.latest_spoken(db_session, master, "fa", 1).id == made["fa"][0].id, "Persian: the approved text, not the newer rewrite"
    assert J.latest_spoken(db_session, master, "de", 1).id == made["de"][1].id, "another language keeps choosing its newest version"


def test_an_archived_spoken_version_is_never_the_source(db_session, versions):
    master, made = versions
    made["fa"][0].status = "archived"
    db_session.commit()
    assert J.latest_spoken(db_session, master, "fa", 1) is None, "the retired version is not picked, and the voice-prepare rewrite still is not"


def test_a_ppie_language_is_never_sent_a_dictionary_and_gets_its_own_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    monkeypatch.setattr(ai_config.voice, "prepared_languages", ["fa"])          # even if the old voice-prepare setting came back
    tts = FakeTTS()
    renderer = VoiceRenderer(provider=tts, use_asr=False, use_listener=False)
    plan = {"language": "fa", "blocks": [_block("FA_a_01", "act1", "بیمه‌نامه‌ی عمر گرفته بود")],
            "pronunciation_dictionary": FILM_DICTIONARY, "pronunciation_rules": FILM_RULES}
    asyncio.run(renderer.render(plan, case_id=7, story_version_id=3))
    assert [(r.pronunciation_dictionaries, r.pronunciation_rules) for r in tts.requests] == [([], [])]
    assert VoiceRenderer.out_dir_for(7, "fa", 3).name == "v3_ppie" and VoiceRenderer.out_dir_for(7, "de", 3).name == "v3"


def test_the_cross_film_lexicon_is_neither_sent_nor_written_by_the_report_only_review(db_session, monkeypatch):
    def never(*a, **k):
        raise AssertionError("the lexicon is not touched by a PPIE review")

    monkeypatch.setattr(SR.LEX, "learn", never)
    monkeypatch.setattr(SR.LEX, "confirm", never)
    reviewed = []

    async def listen(self, result):
        reviewed.append(result["block"]["block_id"])
        return {"verdict": "fail", "errors": [{"type": "pronunciation", "severity": "major", "heard": "omer", "expected": "omr"}],
                "notation_read_aloud": False, "confidence": 0.9}

    async def speak(block, seed):
        raise AssertionError("a report-only review never voices a block again")

    monkeypatch.setattr(SR.Reviewer, "review_block", listen)
    before = db_session.query(PronunciationLexicon).count()
    reviewer = SR.Reviewer(db_session, 1, {}, {}, [], corrects=False)
    out = asyncio.run(reviewer.run({"block": {"block_id": "FA_a_01", "text": "عمر"}, "take": {}}, speak, 1, "fa"))
    assert reviewed == ["FA_a_01"] and out["semantic_review"]["final_status"] == "failed" and "reports only" in out["semantic_review"]["failure"]
    assert db_session.query(PronunciationLexicon).count() == before


def test_the_pronunciation_memory_does_not_reach_a_ppie_planner(db_session, language, monkeypatch):  # noqa: F811
    entry_for(db_session, language, "عمر", status="legacy_unverified", origin="pronunciation_lexicon", expected_irab="اُمر")
    entry_for(db_session, language, "نوه‌هاش", status="human_approved", approved_pronunciation="نَوه‌هاش")
    voicing = Voicing({}, {"b": ["bime", "omr", "navehāsh"]}, {"b": [WordSignal(), WordSignal(), WordSignal()]})
    remembered = Planner(db_session, language, VOICE, MODEL).plan("b", "بیمه عمر نوه‌هاش", voicing)
    assert any(s.name == "earlier_failure" for s in remembered.risks[1].signals) and remembered.decisions[2].replacement == "نَوه‌هاش"
    monkeypatch.setattr(ai_config.ppie, "languages", [language])
    fresh = Planner(db_session, language, VOICE, MODEL).plan("b", "بیمه عمر نوه‌هاش", voicing)
    assert not any(s.name == "earlier_failure" for r in fresh.risks for s in r.signals)
    assert fresh.decisions[2].replacement is None and fresh.text == "بیمه عمر نوه‌هاش", "no approval or correction of an earlier run is applied"


def test_a_ppie_reading_stage_reads_every_block_again_and_keeps_nothing(db_session, stage_env, monkeypatch):  # noqa: F811
    case, version = stage_env
    items = [item("سارا بهم گفت", "B1"), item("او رفت", "B2")]
    calls = {"reader": 0, "write": 0, "check": 0}
    asyncio.run(stage(db_session, case, version, items, calls).run())            # a stored reading and its doubt, as an earlier system left them
    stored = stage(db_session, case, version, items, calls).path
    kept = stored.read_bytes()
    db_session.add(ReadingApproval(case_id=case.id, language="zz", surface="او", kind="spoken_form", sense="", latin="ooo", source="owner"))
    db_session.commit()
    doubts = db_session.query(ReadingDoubt).filter_by(story_version_id=version.id).count()
    monkeypatch.setattr(ai_config.ppie, "languages", ["zz"])
    for run in (1, 2):
        result = asyncio.run(stage(db_session, case, version, items, calls).run())
        assert calls == {"reader": 1 + run, "write": 2 + 2 * run, "check": 2 + 2 * run}, "nothing stored is reused: every block is read again"
        assert result.readings["B2"][0].latin == "x", "an owner approval of an earlier run is not applied"
        assert result.open_doubts == 0 and result.doubts == {"B1": {1}}, "the doubt stays in the run (the planner flags the word), nothing waits"
    assert stored.read_bytes() == kept, "the stored reading is neither read nor written"
    assert db_session.query(ReadingDoubt).filter_by(story_version_id=version.id).count() == doubts
    assert RS.summary(items, result, "zz")["doubts_in_run"] == 1


@pytest.mark.parametrize("text, n, kind, words", [
    ("شماره‌ای که گرفت ۹۱۱ بود", 3, "cardinal", "نهصد و یازده"),
    ("شانزدهم فوریهٔ ۲۰۲۶ بود", 2, "year", "دو هزار و بیست و شش"),
    ("سال ۲۰۱۳ تو فصل دوازدهم", 1, "year", "دو هزار و سیزده"),
    ("مدرک شماره‌ی ۱۰ بود", 2, "cardinal", "ده"),
    ("شماره‌ی ۴۴۸۲۱۹ رو گرفت", 1, "identifier", "چهار چهار هشت دو یک نُه"),
    ("حدود ۱۰۷ هزار پیام", 1, "cardinal", "صد و هفت"),
    ("یه تپانچهٔ ۹ میلی‌متری", 2, "cardinal", "نُه"),
    ("روز ۲۹ سپتامبر", 1, "cardinal", "بیست و نُه"),
    ("نفر ۱۵ام بود", 1, "ordinal", "پانزدهم"),
    ("نفر ۳ام بود", 1, "ordinal", "سوم"),
    ("ساعت ۲:۳۱ بود", 1, "unread", None),
])
def test_a_number_is_read_by_its_context_never_by_frequency(text, n, kind, words):
    said = NUM.read(tokenize(text), n)
    assert (said.kind, said.words) == (kind, words)


def test_every_number_is_read_as_a_persian_number_and_only_a_configured_number_digit_by_digit(monkeypatch):
    assert ai_config.number_reading.digit_by_digit == [], "the owner wants 911 read as a number: nohsad o yazdah"
    for digits, words in (("۹۱۱", "نهصد و یازده"), ("۱۱۲", "صد و دوازده"), ("۲۴", "بیست و چهار")):
        said = NUM.read(tokenize(f"تماس با {digits} گرفت"), 2)
        assert (said.kind, said.words) == ("cardinal", words)
    monkeypatch.setattr(ai_config.number_reading, "digit_by_digit", ["۹۱۱"])           # the mechanism stays: a number the owner lists is said digit by digit
    assert NUM.read(tokenize("تماس با ۹۱۱ گرفت"), 2).kind == "service"


def test_the_planner_gives_the_voice_the_number_words_and_leaves_a_number_it_cannot_read_for_a_person(db_session):
    voicing = Voicing({}, {"b": ["tamās", "bā", "NUM", "gereft"], "c": ["sāat", "NUM", "bud"]}, {})
    planner = Planner(db_session, "fa", VOICE, MODEL)
    plan = planner.plan("b", "تماس با ۹۱۱ گرفت", voicing)
    assert plan.text == "تماس با نهصد و یازده گرفت" and plan.decisions[2].strategy == "number_words" and "cardinal" in plan.decisions[2].basis
    left = planner.plan("c", "ساعت ۲:۳۱ بود", voicing).decisions[1]
    assert (left.strategy, left.needs_review) == ("escalate", True)


def test_a_ppie_run_writes_no_pronunciation_decision(db_session, monkeypatch):
    counts = lambda: [db_session.query(m).count() for m in (PronunciationEntry, PronunciationIntervention, PronunciationEvidence, PronunciationLexicon)]  # noqa: E731
    before = counts()
    voicing = Voicing({}, {"b": ["u", "dar", "tasādof", "mord"]}, {"b": [WordSignal(), WordSignal(), WordSignal(), WordSignal("low", True, False)]})
    planner = Planner(db_session, "fa", VOICE, MODEL)
    plan = planner.plan("b", "او در تصادف مرد", voicing)
    planner.register(plan)
    planner.learn("b", "مرد", heard_right=False, case_id=1)
    assert plan.decisions[3].strategy == "selective_marks" and plan.decisions[3].intervention_id is None
    assert counts() == before


def test_every_persian_style_is_voiced_at_speed_1_2_and_stability_0_2_and_played_1_1_times_faster(tmp_path, monkeypatch):
    for style in ai_config.voice.styles:
        settings = ai_config.voice.settings_for(style, "fa")
        assert (settings["speed"], settings["stability"]) == (1.2, 0.2), style
    assert ai_config.voice.for_language("fa").model_id == "eleven_v4" and ai_config.voice.post_speed["fa"] == 1.1
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    tempos, made = [], A.to_wav

    def spy(raw, wav, rate, t0, t1, tempo=1.0, *fades):
        tempos.append(tempo)
        return made(raw, wav, rate, t0, t1, tempo, *fades)

    monkeypatch.setattr(A, "to_wav", spy)
    tts = FakeTTS()
    asyncio.run(VoiceRenderer(provider=tts, use_asr=False, use_listener=False).render({"language": "fa", "blocks": [_block("FA_a_01", "act1", "او رفت")]}, case_id=7, story_version_id=3))
    assert tempos == [1.1] and (tts.requests[0].settings["speed"], tts.requests[0].settings["stability"]) == (1.2, 0.2)


def test_the_other_languages_keep_their_paths():
    assert ai_config.listens_after_voice("en") and not ai_config.listens_after_voice("fa")
    assert not ai_config.ppie.fresh("en") and not ai_config.ppie.fresh("de") and ai_config.ppie.fresh("ar"), "Arabic is read by PPIE (Tashkeel); German and English have no reading stage"
    assert (ai_config.voice.settings_for("factual", "en")["speed"], ai_config.voice.settings_for("factual", "en")["stability"]) == (0.92, 0.2)


def test_the_approved_story_stays_byte_for_byte_and_the_voice_gets_the_planner_text_only(db_session, versions, tmp_path, monkeypatch):
    """The dry run of the chain the job runs for Persian (the reading agents and the voice are fakes; nothing is paid): source, readings, planner,
    renderer, the run record."""
    from app.documentary import pronunciation_explain as PE

    master, made = versions
    source = J.latest_spoken(db_session, master, "fa", 1)
    sha = hashlib.sha256(source.story_text.encode()).hexdigest()
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    read = ReadingResult({"FA_a_01": [ReadingWord(0, "omr", False, "", "high"), ReadingWord(1, "NUM", False, "", "high")]}, 0)
    voicing = asyncio.run(RS.voicing_of(db_session, "fa", read, []))
    tts = FakeTTS()
    renderer = VoiceRenderer(provider=tts, use_asr=False, use_listener=False)
    renderer.voicing, renderer.planner = voicing, Planner(db_session, "fa", VOICE, MODEL)
    plan = {"language": "fa", "blocks": [_block("FA_a_01", "act1", source.story_text)]}
    asyncio.run(renderer.render(plan, case_id=source.case_id, story_version_id=source.id))
    assert [r.text for r in tts.requests] == ["عمر نهصد و یازده"] and tts.requests[0].pronunciation_dictionaries == []
    record = PE.save_run(db_session, source, "fa", plan, renderer.planner, voicing, renderer.out_dir(source.case_id, "fa", source.id))
    shown = PE.run_record(source, "fa")
    assert record.exists() and shown["fresh"] is True and [(w["word"], w["tts_input"], w["voice_text"]) for w in shown["words"]] == [
        ("۹۱۱", "نهصد و یازده", "عمر نهصد و یازده")]
    db_session.refresh(source)
    assert hashlib.sha256(source.story_text.encode()).hexdigest() == sha


def test_the_voicing_carries_the_settled_boundaries_it_is_given(db_session):
    read = ReadingResult({"FA_a_01": [ReadingWord(0, "omr", False, "", "high")]}, 0)
    boundaries = {"FA_a_01": {0: {"status": "REQUIRED", "reason": "written ezafe", "source": "text"}}}
    voicing = asyncio.run(RS.voicing_of(db_session, "fa", read, [], boundaries))
    assert voicing.boundaries == boundaries and asyncio.run(RS.voicing_of(db_session, "fa", read, [])).boundaries == {}

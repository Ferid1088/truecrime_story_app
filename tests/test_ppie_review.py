"""The review of a Persian narration (ppie_review): approved sources, the English–Persian alignment, the candidate readings, the local analysis copies,
the acoustic decisions and their validation gate, the repair proposals and their independent check, and that no paid step runs without the owner's
authorization. The recognizer and the encoder are fakes here (their real behaviour is measured by the benchmark, not by these tests)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from test_voice_render import FakeTTS, _block

from app.core.ai_config import ai_config
from app.db.models import StoryVersion
from app.documentary.ppie_review import acoustic as AC
from app.documentary.ppie_review import alignment as AL
from app.documentary.ppie_review import candidates as CA
from app.documentary.ppie_review import correction as CO
from app.documentary.ppie_review import expectation as EX
from app.documentary.ppie_review import run as RR
from app.documentary.ppie_review import sources as SRC
from app.documentary.ppie_review import validation as VA
from app.documentary.ppie_review.audio_prep import analysis_copies, digest
from app.documentary.reading.words import tokenize
from app.documentary.voice_render import VoiceRenderer

# --- sources ------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def scratch(db_session, monkeypatch):
    """A scratch case shaped like Case 15: an approved English final, an approved Persian final, its spoken version and a voice-prepare rewrite."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.db.models import Case

    case = Case(canonical_title="Scratch: PPIE review", slug=f"scratch-review-{uuid4().hex[:8]}", status="new", language="fa", origin="scratch")
    db_session.add(case)
    db_session.flush()
    made = lambda **kw: StoryVersion(case_id=case.id, version=1, narrative_angle="a", status="ready", **kw)  # noqa: E731
    english = made(kind="final", language="en", story_text="He came.", approved_at=datetime.now(UTC))
    final = made(kind="final", language="fa", story_text="او آمد.", approved_at=datetime.now(UTC))
    db_session.add_all([english, final])
    db_session.flush()
    spoken = made(kind="spoken", language="fa", story_text="او آمد.", master_version_id=final.id)
    rewrite = made(kind="spoken", language="fa", story_text="او آمَد.", master_version_id=final.id,
                   narrative_structure=json.dumps({"prepared": {"from_version_id": 0}}))
    db_session.add_all([spoken, rewrite])
    db_session.commit()
    monkeypatch.setattr(SRC, "english_final", lambda db, case_id: english if case_id == case.id else None)
    yield case, english, final, spoken, rewrite
    db_session.query(StoryVersion).filter(StoryVersion.case_id == case.id).delete()
    db_session.query(Case).filter_by(id=case.id).delete()
    db_session.commit()


def test_the_approved_sources_are_found_and_a_voice_prepare_rewrite_or_an_unapproved_final_is_refused(db_session, scratch):
    case, english, final, spoken, rewrite = scratch
    found = SRC.approved_sources(db_session, spoken)
    assert (found.narration.id, found.persian_final.id, found.english.id) == (spoken.id, final.id, english.id)
    assert "no approval date of its own" in found.notes[0] and found.describe()["english"]["approved_at"]
    with pytest.raises(SRC.SourceError, match="voice-prepare rewrite"):
        SRC.approved_sources(db_session, rewrite)
    with pytest.raises(SRC.SourceError, match="not a Persian spoken version"):
        SRC.approved_sources(db_session, english)
    final.approved_at = None
    db_session.commit()
    with pytest.raises(SRC.SourceError, match="not approved"):
        SRC.approved_sources(db_session, spoken)


# --- alignment ----------------------------------------------------------------------------------------------------------------------------

def unit_vectors(*rows):
    m = np.array(rows, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


class Encoder:
    """Vectors by a lookup of the sentence text (a fake of the local model)."""

    def __init__(self, table: dict[str, list[float]]):
        self.table = table

    def encode(self, texts):
        return unit_vectors(*[self.table[t] for t in texts])


def test_sentences_keep_their_offsets_and_markers_are_not_text():
    story = "[[ACT:act1]]\n\nاو آمد. [serious] سارا رفت؟ تمام"
    found = AL.segments(story)
    assert [u.text for u in found] == ["او آمد.", "سارا رفت؟", "تمام"] and all(story[u.start:u.end] == u.text for u in found)


@pytest.fixture
def table_config(monkeypatch):
    for k, v in {"band": 1.0, "max_group": 2, "match_floor": 0.5, "aligned_min": 0.8, "partial_min": 0.5, "ambiguous_margin": 0.05}.items():
        monkeypatch.setattr(ai_config.ppie_review.alignment, k, v)


def test_one_to_one_many_to_one_and_a_persian_sentence_with_no_english(table_config):
    fa = "الف یک. الف دو. ب. ج اضافه. د."
    en = "A one and two. B. D."
    table = {"الف یک.": [1, 0.2, 0, 0, 0], "الف دو.": [1, -0.2, 0, 0, 0], "ب.": [0, 0, 1, 0, 0], "ج اضافه.": [0, 0, 0, 0, 1], "د.": [0, 0, 0, 1, 0],
             "A one and two.": [1, 0, 0, 0, 0], "B.": [0, 0, 1, 0, 0], "D.": [0, 0, 0, 1, 0]}
    persian, english, matches = AL.prepare(fa, en, Encoder(table))
    shape = [(m.persian, m.english, m.status) for m in matches]
    assert shape == [((0, 2), (0, 1), "ALIGNED"), ((2, 3), (1, 2), "ALIGNED"), ((3, 4), None, "MISSING_REFERENCE"), ((4, 5), (2, 3), "ALIGNED")]


def test_a_match_whose_numbers_differ_is_not_aligned_and_a_close_rival_makes_it_ambiguous(table_config):
    persian = AL.segments("در سال ۲۰۲۶ رفت. او آمد.")
    english = AL.segments("He left in 2025. He came. He came back.")
    m = AL.judge(AL.Match((0, 1), (0, 1), 0.95, 0.1, ""), persian, english)
    assert m.status == "PARTIAL" and "numbers differ" in m.issues[0]
    rival = AL.judge(AL.Match((1, 2), (1, 2), 0.9, 0.88, ""), persian, english)
    assert rival.status == "AMBIGUOUS"


def test_the_verifier_agent_can_overrule_code_and_a_block_gets_the_worst_meaningful_status(table_config):
    persian, english = AL.segments("الف. ب."), AL.segments("A. B.")
    matches = [AL.Match((0, 1), (0, 1), 0.9, 0.1, "ALIGNED"), AL.Match((1, 2), (1, 2), 0.9, 0.1, "ALIGNED")]

    async def agent(payload):
        return {"status": "MISMATCH", "differences": ["another event"]} if payload["persian"] == "ب." else {"status": "ALIGNED"}

    checked = asyncio.run(AL.verify(matches, persian, english, agent))
    assert [m.status for m in checked] == ["ALIGNED", "MISMATCH"] and checked[1].verified_by == "agent" and "another event" in checked[1].issues
    found = AL.blocks([{"block_id": "b1", "text": "الف."}, {"block_id": "b2", "text": "[calm] ب."}, {"block_id": "b3", "text": "نیست"}],
                      "الف. ب.", "A. B.", persian, english, checked)
    assert [(b.block_id, b.status) for b in found] == [("b1", "ALIGNED"), ("b2", "MISMATCH"), ("b3", "MISSING_REFERENCE")]
    assert found[0].english_text == "A." and found[0].persian_span == (0, 4)
    assert AL.block_status(["ALIGNED", "MISSING_REFERENCE"]) == "PARTIAL" and AL.block_status(["ALIGNED", "AMBIGUOUS"]) == "AMBIGUOUS"


# --- the readings a word is scored against ------------------------------------------------------------------------------------------------

def test_the_candidates_hold_the_errors_the_owner_heard():
    kinds = lambda reading: {c.reading: c.kind for c in CA.for_word(reading)}  # noqa: E731
    assert kinds("omr")["omer"] == "epenthesis" and kinds("dar-e")["dare-ye"] == "ezafe_ye" and kinds("navehāsh")["novehāsh"] == "vowel"
    assert CA.for_word("omr")[0].kind == "expected" and kinds("melk")["melk-e"] == "ezafe_added"
    assert "omr" not in CA.epenthesis("sh") and CA.epenthesis("ghatl") == ["ghatel"] and CA.epenthesis("dāsh") == []


def test_the_reading_agent_never_sees_ppies_reading_and_an_unanswered_word_is_unsettled():
    body = EX.payload("بیمه‌نامه‌ی عمر", "قبل", "بعد", "a life insurance policy", "ALIGNED")
    assert set(body) == {"words", "previous", "next", "english", "alignment_status"} and body["words"][1] == {"n": 1, "text": "عمر"}
    got = EX.parse({"words": [{"n": 1, "reading": "omr", "alternatives": ["omer", "omr"], "reason": "life"}]}, "بیمه‌نامه‌ی عمر")
    assert (got[0].settled, got[0].reading) == (False, "") and (got[1].reading, got[1].alternatives, got[1].settled) == ("omr", ["omer"], True)
    assert got[1].describe()["expected_phonemes"] == "omɾ"


# --- local analysis copies ----------------------------------------------------------------------------------------------------------------

def test_the_analysis_copies_are_local_pitch_kept_and_removed_and_the_original_is_never_written(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.ppie_review, "analysis_speeds", [1.0, 0.8])          # the mechanism with a slower copy (the production config makes none)
    original = tmp_path / "take.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=180:duration=2", "-ar", "44100", str(original)], check=True)
    before = digest(original)
    with analysis_copies(original, tmp_path) as copies:
        folder = copies[1.0].path.parent
        assert set(copies) == {1.0, 0.8} and all(c.usable for c in copies.values())
        assert abs(copies[0.8].pitch_shift_cents) <= ai_config.ppie_review.pitch_tolerance_cents
        seconds = {s: float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(c.path)],
                                           capture_output=True, text=True, check=True).stdout) for s, c in copies.items()}
        assert seconds[0.8] == pytest.approx(seconds[1.0] / 0.8, rel=0.02)
        assert subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0", str(copies[0.8].path)],
                              capture_output=True, text=True, check=True).stdout.strip() == f"{ai_config.ppie_review.analysis_rate},1"
    assert digest(original) == before and not folder.exists()


# --- acoustic decisions -------------------------------------------------------------------------------------------------------------------

class Listener:
    """A fake recognizer: the loss of each sound string is given; frames are fixed phones."""

    name = "fake"
    FRAME = 0.02

    def __init__(self, losses: dict[str, float]):
        self.losses = losses

    def fit(self, log_probs, start, end, ipa):
        if ipa not in self.losses:
            raise ValueError("does not fit")
        return self.losses[ipa] * round((end - start) / self.FRAME)

    @staticmethod
    def between(frames, start, end):
        return [t for t, s, e in frames if e > start and s < end]


def judged(losses: dict[str, float], validated: bool, expected: str | None = "omr", window=(1.0, 1.4)):
    word = tokenize("عمر")[0]
    listener = Listener({CA.made(k, "x").ipa: v for k, v in losses.items()})
    return AC.review_word(word, window, expected, CA.for_word(expected) if expected else [], {1.0: None, 0.8: None}, listener,
                          [("o", 1.0, 1.1), ("m", 1.1, 1.2)], validated)


def test_a_rival_reading_that_fits_clearly_better_is_a_suspected_error_and_only_a_validated_recognizer_confirms_it():
    advisory = judged({"omr": 2.0, "omer": 1.0}, validated=False)
    assert (advisory.suggested, advisory.status) == ("FAIL", "UNCERTAIN") and "heard as omer (epenthesis)" in advisory.evidence[0]
    assert advisory.describe()["advisory"] is True and advisory.heard == "o m"
    assert judged({"omr": 2.0, "omer": 1.0}, validated=True).status == "FAIL"
    assert judged({"omr": 1.0, "omer": 2.0}, validated=True).status == "PASS"
    assert judged({"omr": 1.0, "omer": 1.1}, validated=True).status == "UNCERTAIN", "no clear winner is never a pass"


def test_a_word_without_timing_or_expected_reading_or_room_is_not_evaluable():
    assert judged({"omr": 1.0}, True, window=None).status == "NOT_EVALUABLE"
    assert judged({"omr": 1.0}, True, expected=None).evidence[-1] == "no independent expected reading in this run"
    assert judged({"omr": 1.0}, True, window=(1.0, 1.0)).status == "NOT_EVALUABLE"
    assert judged({"omer": 1.0}, True).evidence[-1] == "the expected reading's sounds do not fit the window"


def test_each_approved_word_gets_its_window_in_the_take_even_when_a_number_was_said_as_words():
    voice = "تماس با نُه یک یک گرفت"
    timing = {"characters": list(voice), "starts": [i * 0.1 for i in range(len(voice))], "ends": [i * 0.1 + 0.1 for i in range(len(voice))]}
    spans = AC.windows("تماس با ۹۱۱ گرفت", voice, timing)
    assert voice[spans[2].char_start:spans[2].char_end] == "نُه یک یک" and spans[3].window == pytest.approx((1.8, 2.2))
    assert AC.windows("تماس با ۹۱۱ گرفت", voice, {**timing, "characters": list("other")}) == {}
    assert RR.differing_words("تماس با ۹۱۱ گرفت", voice) == [] and RR.differing_words("کالب آمد", "کیلب آمد") == ["کالب → کیلب"]
    other = "و نوه‌هاش تو خونه بودن"
    other_timing = {"characters": list(other), "starts": [i * 0.1 for i in range(len(other))], "ends": [i * 0.1 + 0.1 for i in range(len(other))]}
    found = AC.windows("نوه‌هاش تو خونه موندن", other, other_timing)
    assert set(found) == {0, 1, 2}, "a voice text with an extra and a changed word still times every word they share"
    assert RR.differing_words("نوه‌هاش تو خونه موندن", other) == ["موندن → (another word, or none)"]


# --- the validation gate ------------------------------------------------------------------------------------------------------------------

def test_the_validation_measures_detection_and_never_counts_uncertain_as_right():
    rows = [(True, "FAIL"), (True, "UNCERTAIN"), (False, "PASS"), (False, "FAIL"), (True, "PASS")]
    m = VA.metrics(rows)
    assert (m["detected"], m["false_alarms"], m["missed"], m["uncertain"]) == (1, 1, 1, 1)
    assert (m["precision"], m["recall"], m["false_positive_rate"]) == (0.5, round(1 / 3, 3), 0.5)
    assert VA.decide({"a": 1.0, "b": 2.0}, "a", 0.5) == "PASS" and VA.decide({"a": 2.0, "b": 1.0}, "a", 0.5) == "FAIL"
    assert VA.decide({"a": 1.0}, "a", 0.5) == "NOT_EVALUABLE" and [VA.held_out(i) for i in (1, 2, 3)] == [VA.held_out(i) for i in (1, 2, 3)]


def test_without_a_passing_benchmark_the_recognizer_is_unvalidated(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.ppie_review.validation, "report_file", "none.json")
    monkeypatch.setattr(VA, "BASE", tmp_path)
    assert VA.current()["validated"] is False


# --- repairs ------------------------------------------------------------------------------------------------------------------------------

def review_of(suggested: str, best: str = "omer", kind: str = "epenthesis", word: str = "عمر") -> dict:
    return {"word": word, "suggested": suggested, "advisory": True,
            "candidates": [{"reading": "omr", "kind": "expected", "loss": {"1.0": 2.0}}, {"reading": best, "kind": kind, "loss": {"1.0": 1.0}}]}


def test_the_diagnosis_tells_ppie_from_the_voice_and_says_when_the_recognizer_may_be_wrong():
    cause, evidence = CO.diagnose(review_of("FAIL"), "omr", {"reading": "omr", "tts_input": "عمر"}, "ALIGNED", False)
    assert cause == "tts_misreading" and "may be a false alarm" in evidence[0]
    assert CO.diagnose(review_of("FAIL"), "omr", {"reading": "omar", "tts_input": "عمر"}, "ALIGNED", False)[0] == "ppie_wrong_reading"
    assert CO.diagnose(review_of("FAIL", "dare-ye", "ezafe_ye", "در"), "dar-e", {"reading": "dar-e", "tts_input": "دَره"}, "ALIGNED", False)[0] == \
        "inserted_vowel_or_ezafe"
    assert CO.diagnose(review_of("FAIL"), "noh yek yek", None, "AMBIGUOUS", True)[0] == "number_normalization"
    assert CO.diagnose(review_of("UNCERTAIN"), "omr", None, "ALIGNED", False)[0] == "unknown"


def test_the_candidates_change_only_the_words_voice_form_and_a_failed_form_is_not_proposed_again():
    voice = "یه بیمه‌نامه‌ی عمر گرفت"
    s, e = voice.index("عمر"), voice.index("عمر") + 3
    first = CO.propose("b", 2, "عمر", "omr", "tts_misreading", [], voice, (s, e), [])
    forms = {x["type"]: x["form"] for x in first.strategies}
    assert forms["inline_ipa"] == "/omɾ/" and "<phoneme" not in json.dumps(first.describe())
    assert all(x["tts_text"].replace(x["form"], "عمر") == voice for x in first.strategies), "nothing but the word changes"
    assert first.strategies[0]["type"] == ai_config.ppie_review.correction.strategies[0]
    again = CO.propose("b", 2, "عمر", "omr", "tts_misreading", [], voice, (s, e), [{"form": forms["inline_ipa"], "outcome": "failed"}])
    assert forms["inline_ipa"] not in [x["form"] for x in again.strategies]
    spent = [{"form": f"x{i}", "outcome": "failed"} for i in range(ai_config.ppie_review.correction.max_attempts_per_word)]
    assert CO.propose("b", 2, "عمر", "omr", "tts_misreading", [], voice, (s, e), spent).status == "OWNER_REVIEW"
    assert CO.propose("b", 2, "عمر", "omr", "recognizer_false_positive", [], voice, (s, e), []).status == "OWNER_REVIEW"


def test_the_corrector_agent_can_only_order_code_candidates_and_can_send_the_word_to_the_owner(db_session, monkeypatch):
    from app.documentary.reading import ask as ASK

    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: __import__("contextlib").nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())
    voice = "عمر"

    async def agent(gen, body):
        return {"cause": "tts_misreading", "ranking": [{"strategy": "inline_ipa"}, {"strategy": "made_up"}]}, None

    p = asyncio.run(CO.rank(db_session, 1, CO.propose("b", 0, "عمر", "omr", "unknown", [], voice, (0, 3), []), {}, agent))
    assert p.strategies[0]["type"] == "inline_ipa" and p.cause == "tts_misreading" and {s["type"] for s in p.strategies} <= set(CO.forms("عمر", "omr"))

    async def doubter(gen, body):
        return {"cause": "reading_agent_wrong", "evidence": "the word is the name Omar"}, None

    p = asyncio.run(CO.rank(db_session, 1, CO.propose("b", 0, "عمر", "omr", "unknown", [], voice, (0, 3), []), {}, doubter))
    assert (p.status, p.strategies) == ("OWNER_REVIEW", [])


def test_a_repair_is_checked_by_code_and_an_unvalidated_check_leaves_the_decision_to_the_owner():
    before = [{"n": 0, "word": "عمر", "suggested": "FAIL", "model_validation": "UNVALIDATED"},
              {"n": 1, "word": "گرفت", "suggested": "PASS", "model_validation": "UNVALIDATED"}]
    after = [{**before[0], "suggested": "PASS"}, before[1]]
    assert CO.verify(before, after, 0, 2.0, 2.1)["final_decision"] == "UNCERTAIN"
    valid = lambda rows: [{**r, "model_validation": "VALIDATED"} for r in rows]  # noqa: E731
    assert CO.verify(valid(before), valid(after), 0, 2.0, 2.1)["final_decision"] == "ACCEPTABLE"
    regressed = valid([after[0], {**before[1], "suggested": "FAIL"}])
    checked = CO.verify(valid(before), regressed, 0, 2.0, 2.1)
    assert (checked["final_decision"], checked["new_errors_detected"]) == ("REJECTED", ["گرفت"])
    assert CO.verify(valid(before), valid(after), 0, 2.0, 3.0)["final_decision"] == "REJECTED"


# --- authorization, takes and the assembly --------------------------------------------------------------------------------------------

@pytest.fixture
def narration(tmp_path, monkeypatch, db_session, scratch):
    """A two-block Persian narration of the scratch case voiced by a fake voice, and a review run on it."""
    case, english, final, spoken, rewrite = scratch
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    blocks = [_block("FA_a_01", "act1", "یه بیمه‌نامه‌ی عمر گرفت."), _block("FA_a_02", "act1", "او رفت.")]
    plan = {"language": "fa", "blocks": blocks}
    monkeypatch.setattr(RR, "performance_for_version", lambda db, version: plan)
    renderer = VoiceRenderer(provider=FakeTTS(), use_asr=False, use_listener=False)
    renderer.text_overrides = {b["block_id"]: b["text"] for b in blocks}
    asyncio.run(renderer.render(plan, case_id=case.id, story_version_id=spoken.id))
    folder = renderer.out_dir(case.id, "fa", spoken.id)
    return RR.ReviewRun(db_session, spoken, folder, "test-run"), plan, folder


def test_no_paid_step_runs_without_the_owners_authorization_and_limit(narration):
    run, _, _ = narration
    with pytest.raises(RR.NotAuthorized, match="not authorized"):
        run.allow("agents")
    run.authorize(RR.Authorization(agents=True, tts=False, max_eur=0.0, by="owner"))
    with pytest.raises(RR.NotAuthorized, match="above 0 EUR"):
        run.allow("agents")
    run.authorize(RR.Authorization(agents=True, tts=True, max_eur=0.5, by="owner"))
    run.allow("agents", 0.1)
    with pytest.raises(RR.NotAuthorized, match="may spend 0.5"):
        run.allow("tts", 0.6)


def test_a_repair_take_is_voiced_only_when_authorized_and_the_narration_changes_only_when_the_owner_keeps_it(narration, monkeypatch):
    run, plan, folder = narration
    takes = RR.takes(folder, plan["blocks"])
    assert set(takes) == {"FA_a_01", "FA_a_02"} and takes["FA_a_01"]["text"] == plan["blocks"][0]["text"]
    voice = takes["FA_a_01"]["text"]
    s = voice.index("عمر")
    run.write("proposals.json", [CO.propose("FA_a_01", 2, "عمر", "omr", "tts_misreading", [], voice, (s, s + 3), []).describe()])
    run.write("acoustic.json", {"FA_a_01": [{"n": 2, "word": "عمر", "expected": "omr", "suggested": "FAIL", "model_validation": "UNVALIDATED"}]})
    tts = FakeTTS()
    with pytest.raises(RR.NotAuthorized):
        asyncio.run(run.generate("FA_a_01", 2, 1, provider=tts, listener=Listener({})))
    assert tts.requests == [], "nothing is voiced without authorization"
    run.authorize(RR.Authorization(tts=True, max_eur=1.0, by="owner"))
    monkeypatch.setattr(run, "review_take", lambda text, take, expected, listener, validation: [])
    attempt = asyncio.run(run.generate("FA_a_01", 2, 1, provider=tts, listener=Listener({})))
    assert [r.text for r in tts.requests] == [voice.replace("عمر", "/omɾ/")] and attempt["verification"]["final_decision"] == "UNCERTAIN"
    assert RR.takes(folder, plan["blocks"])["FA_a_01"]["text"] == voice, "the narration still has the original take"
    with pytest.raises(RR.NotAuthorized):
        asyncio.run(run.generate("FA_a_01", 2, 1, provider=tts, listener=Listener({})))
    before = (folder / "narration.mp3").read_bytes()
    kept = asyncio.run(run.accept("FA_a_01", 2, 0, "owner"))
    assert kept["status"] == "ACCEPTED" and RR.takes(folder, plan["blocks"])["FA_a_01"]["text"] == voice.replace("عمر", "/omɾ/")
    assert (folder / "before-test-run" / "narration.mp3").read_bytes() == before and len(tts.requests) == 1, "the assembly voiced nothing"


def test_the_assembly_voice_refuses_every_request():
    with pytest.raises(RR.NotAuthorized):
        asyncio.run(RR.NoVoice().synthesize(None))
    assert not Path(ai_config.ppie_review.folder).is_absolute()


# --- controlled recordings and blind labelling --------------------------------------------------------------------------------------------

def test_a_controlled_item_differs_only_in_its_target_word_and_its_clip_is_labelled_blind(db_session, tmp_path, monkeypatch):
    from app.db.models import PhoneticSample
    from app.documentary import phonetic_benchmark as PB
    from app.documentary.ppie_review import controlled as CB

    monkeypatch.setattr(PB, "BASE", tmp_path)
    monkeypatch.setattr(CB, "load", lambda name: ("t_set", [CB.Item("ep01", "epenthesis", "او تمام عمر اینجا بود.", "عمر", "omr", "omer")]))
    right, wrong = CB.request_text(CB.load("x")[1][0], "omr")[0], CB.request_text(CB.load("x")[1][0], "omer")[0]
    assert right == "او تمام /omɾ/ اینجا بود." and wrong == "او تمام /omeɾ/ اینجا بود."
    settings = CB.request(CB.load("x")[1][0], "omr").settings
    assert (settings["speed"], settings["stability"]) == (1.2, 0.2)
    tts = FakeTTS()
    result = asyncio.run(CB.build(db_session, "x", VoiceRenderer(provider=tts, use_asr=False, use_listener=False), None))
    try:
        assert result["clips"] == 2 and len(tts.requests) == 2 and (tmp_path / "controlled" / "t_set" / "ep01_wrong.wav").exists()
        again = asyncio.run(CB.build(db_session, "x", VoiceRenderer(provider=tts, use_asr=False, use_listener=False), None))
        assert len(tts.requests) == 2 and again["paid_takes"] == 0 and again["new_clips"] == 0, "a take is never paid twice"
        sample = db_session.get(PhoneticSample, result["samples"][1]["id"])
        assert (sample.purpose, sample.intended_key, sample.expected_key) == ("validation", "omer", "omr")
        shown = PB.describe(db_session, sample)
        assert shown["blind"] and shown["expected_key"] is None and shown["intended_key"] is None and shown["opinions"] == []
        PB.human_label(db_session, sample.id, "omer", "owner")
        shown = PB.describe(db_session, sample)
        assert not shown["blind"] and (shown["expected_key"], shown["intended_key"]) == ("omr", "omer")
    finally:
        db_session.query(PB.PhoneticLabel).filter(PB.PhoneticLabel.sample_id.in_([s["id"] for s in result["samples"]])).delete(synchronize_session=False)
        db_session.query(PhoneticSample).filter(PhoneticSample.id.in_([s["id"] for s in result["samples"]])).delete(synchronize_session=False)
        db_session.commit()


def test_a_known_problem_clip_never_counts_in_the_validation(db_session, monkeypatch):
    from app.db.models import PhoneticSample

    known = PhoneticSample(language="fa-test", source="case_audio", surface="عمر", audio_path="known.wav", candidates="[]", expected_key="omr",
                           status="human_verified", human_key="omer", purpose="known_problem")
    db_session.add(known)
    db_session.commit()
    try:
        report = VA.evaluate(db_session, Listener({}), language="fa-test")
        assert report["labelled_clips"] == 0 and report["validated"] is False
    finally:
        db_session.delete(known)
        db_session.commit()


def test_a_take_in_which_the_voice_read_the_notation_aloud_is_rejected_even_unvalidated():
    before = [{"n": 0, "word": "حکم", "suggested": "FAIL", "model_validation": "UNVALIDATED", "heard": "h o k o m"}]
    after = [{**before[0], "suggested": "UNCERTAIN", "heard": "s l æ ʃ eɪ tʃ oʊ k eɪ ɛ m s l æ ʃ"}]
    checked = CO.verify(before, after, 0, 2.0, 2.2)
    assert checked["final_decision"] == "REJECTED" and "notation aloud" in checked["issues"][0]


def test_a_take_in_which_the_voice_read_the_notation_aloud_is_rejected_even_unvalidated():
    before = [{"n": 0, "word": "حکم", "suggested": "FAIL", "model_validation": "UNVALIDATED", "heard": "h o k o m"}]
    after = [{**before[0], "suggested": "UNCERTAIN", "heard": "s l æ ʃ eɪ tʃ oʊ k eɪ ɛ m s l æ ʃ"}]
    checked = CO.verify(before, after, 0, 2.0, 2.2)
    assert checked["final_decision"] == "REJECTED" and "notation aloud" in checked["issues"][0]


def test_the_review_listens_to_the_finished_block_at_its_production_speed_never_to_a_slowed_copy(monkeypatch):
    from app.documentary.ppie_review.run import final_alignment

    monkeypatch.setattr(ai_config.voice, "post_speed", {"fa": 1.1})
    monkeypatch.setattr(ai_config.voice, "lead_pad_ms", 60)
    raw = {"characters": list("ab c"), "starts": [1.1, 1.65, 2.2, 2.75], "ends": [1.65, 2.2, 2.75, 3.3]}
    final = final_alignment(raw, "fa")
    assert final["characters"] == raw["characters"]
    # every time is divided by the post-speed (1.1) and the start the renderer cut off (first word at 1.0 s, minus a 60 ms lead) is taken away
    assert [round(t, 3) for t in final["starts"]] == [0.06, 0.56, 1.06, 1.56]
    assert [round(t, 3) for t in final["ends"]] == [0.56, 1.06, 1.56, 2.06]
    assert ai_config.ppie_review.analysis_speeds == [1.0], "no slowed or other-speed copy of the finished audio is made"


def test_two_rival_readings_that_fit_equally_well_do_not_stop_the_review():
    from app.documentary.ppie_review.candidates import for_word

    rivals = [c.reading for c in for_word("omr") if c.kind != "expected"]
    assert len(rivals) >= 2, "the case needs two rivals"
    tied = {"omr": 2.0, **{r: 1.0 for r in rivals}}
    assert judged(tied, validated=True).suggested == "FAIL"


def test_a_paid_reading_of_this_run_is_read_back_as_it_was_written():
    from app.documentary.ppie_review import expectation as EX

    e = EX.Expectation(3, "عمر", "omr", ["omer"], "context", True)
    assert EX.stored(e.describe()) == e

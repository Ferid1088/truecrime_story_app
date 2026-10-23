# ruff: noqa: F811  (`setup` is the flow tests' fixture, imported)
"""The delivered text is checked, not only gated: a review by the inspector, the evidence audit and the
correction of confirmed findings for English, the meaning check against the approved English text for
every other language, one revision round, an open status instead of an automatic approval when a blocking
item is left, and every other language re-told from the approved English final."""

import asyncio
import json

import pytest
from test_language_pipeline_flow import ACTS, FakeGen, _marked, _pipeline, setup  # noqa: F401

from app.core.ai_config import ai_config
from app.db.models import Case, Fact, StoryPlan, StoryVersion
from app.language_pipeline import review
from app.language_pipeline.audit_fix import AuditFix, FixAnswer, FixDecision, FixEdit, carries
from app.language_pipeline.lengths import final_range
from app.language_pipeline.stages import StageFailed
from app.language_pipeline.state import case_state
from app.language_pipeline.store import (
    FIDELITY,
    FINAL,
    INSPECTION,
    REVIEW,
    REWRITE,
    StageStore,
    pipeline_of,
)
from app.utils import utc_now


def run(gen, case, languages=("en",)):
    return asyncio.run(_pipeline(gen).run_case(case.id, list(languages), 60))


def rows(db_session, case, **filters):
    db_session.expire_all()
    return db_session.query(StoryVersion).filter_by(case_id=case.id, **filters).order_by(StoryVersion.id).all()


def fid(severity="high", kind="changed_fact"):
    return {"id": 1, "severity": severity, "kind": kind, "act": "act1", "quote": "q", "english": "e",
            "problem": "the meaning differs", "correction": "return to the English"}


def finding(severity="high", quote="enact1final0 enact1final1", act="act1", kind="wrong_detail"):
    return {"id": 1, "severity": severity, "kind": kind, "act": act, "quote": quote, "claim": "c",
            "evidence_ids": ["F001"], "evidence": "e", "problem": "p", "correction": "remove"}


# ------------------------------------------------------------------------------ the review of the delivered text
def test_the_inspector_reads_the_delivered_final_and_a_review_row_records_it(setup, db_session):
    gen = FakeGen()
    out = run(gen, setup)
    (final,) = rows(db_session, setup, kind=FINAL)
    (review_row,) = rows(db_session, setup, kind=REVIEW)
    assert json.loads(review_row.narrative_structure)["pipeline"]["checks_version_id"] == final.id
    inspected = [c["user"]["story"] for c in gen.calls if c["role"] == "language_inspector"]
    assert inspected[-1] == final.story_text and inspected[0] != final.story_text or len(inspected) == 2
    assert out["languages"][0]["status"] == "done" and final.approved_by == "automatic"
    assert json.loads(final.critic_notes)["checks"] == {
        "review_counts": {"medium": 1}, "check_counts": {}, "open_issues": [], "revision": False}


def test_a_high_item_in_the_review_starts_one_revision_and_a_clean_second_review_approves(setup, db_session):
    class HighOnce(FakeGen):
        async def generate_structured(self, role, system, user, images=None, **k):
            task = json.loads(user)
            if role == "language_inspector":
                n = self.inspections[task["language"]] = self.inspections.get(task["language"], 0) + 1
                severity = {1: "medium", 2: "high"}.get(n, "low")        # inspection, review, re-review
                return ({"verdict": "revise", "scores": {k: 80 for k in ("native_naturalness", "spoken_quality",
                         "narrative_flow", "engagement")}, "summary": "ok",
                         "items": [{"id": 1, "severity": severity, "kind": "flow", "act": "act1", "quote": "x",
                                    "problem": "p", "instruction": "i"}]}, self._result("{}", role))
            return await super().generate_structured(role, system, user, images, **k)

    gen = HighOnce()
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done"
    finals = rows(db_session, setup, kind=FINAL)
    assert [json.loads(f.narrative_structure)["pipeline"].get("stage") for f in finals] == ["final", "revision"]
    assert finals[0].approved_at is None and finals[1].approved_by == "automatic"
    revision_call = [c for c in gen.calls if c["role"] == "language_final_writer"][1]
    assert revision_call["user"]["last_version"] == finals[0].story_text           # starts from the delivered text
    assert revision_call["user"]["critique"]["items"][0]["severity"] == "high"
    assert len(rows(db_session, setup, kind=REVIEW)) == 2                           # the revision is reviewed again


class LosesMarkers(FakeGen):
    """The final writer loses the plan's act markers on its revision call(s) — case 15 v237: a
    /revise round produced prose with no [[ACT:id]] markers at all, saved anyway as a new, broken
    version. `fail_times` calls lose the markers; the next one is clean."""

    def __init__(self, fail_times: int):
        super().__init__()
        self.fail_times = fail_times
        self.revision_calls = 0

    async def generate_text(self, role, system, user, images=None, **k):
        task = json.loads(user)
        if role == "language_final_writer" and task.get("critique", {}).get("summary", "").startswith("Revision"):
            self.revision_calls += 1
            self.calls.append({"role": role, "system": system, "user": task, "raw": user, **k})
            if self.revision_calls <= self.fail_times:
                return self._result("Prose with no act markers at all, lost in the rewrite.", role)
            words = task.get("target_words_min") or 100
            per_act = int((words + 20) / len(ACTS))
            return self._result(_marked(per_act, "fixedx"), role)
        return await super().generate_text(role, system, user, images, **k)


def test_a_revision_that_loses_the_act_markers_is_retried_once_then_saved_clean(setup, db_session):
    gen = LosesMarkers(fail_times=1)
    gen.review_severity = "high"
    run(gen, setup)
    assert gen.revision_calls == 2                                    # the lost-markers call, then one retry
    finals = rows(db_session, setup, kind=FINAL)
    assert len(finals) == 2                                           # the retry's clean text was saved
    revision = finals[-1]
    assert pipeline_of(revision).get("stage") == "revision"
    assert "fixedx" in revision.story_text and "no act markers" not in revision.story_text


def test_a_revision_that_loses_the_act_markers_twice_keeps_the_previous_version(setup, db_session):
    gen = LosesMarkers(fail_times=99)                                  # never recovers
    gen.review_severity = "high"
    run(gen, setup)
    assert gen.revision_calls == 2                                    # retried once, never a third time
    finals = rows(db_session, setup, kind=FINAL)
    assert len(finals) == 1                                           # no broken "revision" version was saved
    assert "no act markers" not in finals[0].story_text


def test_a_blocking_item_that_remains_after_the_one_revision_leaves_the_text_open_and_unapproved(setup, db_session):
    gen = FakeGen()
    gen.review_severity = "high"
    out = run(gen, setup)
    english = out["languages"][0]
    assert english["status"] == "open" and english["open_issues"] == 1
    finals = rows(db_session, setup, kind=FINAL)
    assert len(finals) == 2 and gen.count("language_final_writer") == 2             # one revision, never a second
    assert all(f.approved_at is None for f in finals) and finals[-1].status == "open_issues"
    notes = json.loads(finals[-1].critic_notes)
    assert notes["checks"]["open_issues"][0]["source"] == "review" and notes["checks"]["revision"] is True
    state = case_state(db_session, setup.id)
    block = next(b for b in state["languages"] if b["language"] == "en")
    assert block["open"] is True and block["approved"] is False and block["stages"]["review"]["id"]


def test_a_language_with_no_approved_english_base_says_why_instead_of_looking_blank(setup, db_session):
    """de/fa/ar are re-told from the approved English final; while English is stuck open they have no
    stages at all (`case_state` returns `"stages": {}` for them) — indistinguishable in the UI from
    "not started" or "stuck" without a reason. `waiting_reason` names the English version, the revision
    round and the open-item count."""
    gen = FakeGen()
    gen.review_severity = "high"
    out = run(gen, setup, ("en", "fa"))
    assert out["languages"][0]["status"] == "open"
    assert out["languages"][1]["status"] == "failed"               # fa never started: no base to re-tell from

    state = case_state(db_session, setup.id)
    fa = next(b for b in state["languages"] if b["language"] == "fa")
    assert fa["stages"] == {}
    english_final = rows(db_session, setup, kind=FINAL, language="en")[-1]
    assert fa["waiting_reason"] == (
        f"Waiting for the English story to be approved — version {english_final.id}, round 2, "
        "1 open item(s) of blocking severity.")

    en = next(b for b in state["languages"] if b["language"] == "en")
    assert en["waiting_reason"] is None                             # en has its own stages; no reason needed


def test_markers_and_length_alone_no_longer_approve_a_text(setup, db_session, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.review, "blocking_severities", ["medium"])
    out = run(FakeGen(), setup)                  # the fake inspector always has a medium item
    assert out["languages"][0]["status"] == "open"
    (final,) = [f for f in rows(db_session, setup, kind=FINAL)][-1:]
    assert final.status == "open_issues" and final.approved_at is None
    assert json.loads(final.critic_notes)["quality_gates"]["pass"] is True        # the gates passed; that is not enough


# ------------------------------------------------------------------------------ every other language
def test_a_language_is_re_told_from_the_approved_english_final_and_checked_against_it(setup, db_session):
    gen = FakeGen()
    out = run(gen, setup, ("en", "fa"))
    assert [lg["status"] for lg in out["languages"]] == ["done", "done"]
    english = next(f for f in rows(db_session, setup, kind=FINAL) if f.language == "en")
    rewrite_call = next(c for c in gen.calls if c["role"] == "language_rewrite_writer" and c["user"]["language"] == "fa")
    assert rewrite_call["user"]["english_master"] == english.story_text
    check = next(c for c in gen.calls if c["role"] == "english_evidence_auditor" and "story" in c["user"])
    assert list(check["user"]) == ["language", "english_master", "story"]         # two texts; no evidence
    assert check["user"]["english_master"] == english.story_text
    assert "evidence" not in json.dumps(check["user"])
    (fa_final,) = [f for f in rows(db_session, setup, kind=FINAL) if f.language == "fa"]
    derived = json.loads(fa_final.narrative_structure)["pipeline"]["derived_from"]
    assert (derived["id"], derived["kind"], derived["text_hash"]) == (english.id, "final", english.text_hash)
    assert fa_final.draft_version_id == english.id


def test_a_language_does_not_start_when_english_is_open(setup, db_session):
    gen = FakeGen()
    gen.review_severity = "high"
    out = run(gen, setup, ("fa", "en"))
    assert [(lg["language"], lg["status"]) for lg in out["languages"]] == [("fa", "failed"), ("en", "open")]
    assert "no approved en final" in out["languages"][0]["error"]
    assert gen.count("language_rewrite_writer") == 1 and not rows(db_session, setup, language="fa")


def test_a_high_difference_in_meaning_starts_one_revision_and_the_result_is_checked_again(setup, db_session):
    gen = FakeGen()
    gen.fidelity_items = [[fid("high")], []]
    out = run(gen, setup, ("en", "de"))
    assert [lg["status"] for lg in out["languages"]] == ["done", "done"]
    finals = [f for f in rows(db_session, setup, kind=FINAL) if f.language == "de"]
    assert [json.loads(f.narrative_structure)["pipeline"].get("stage") for f in finals] == ["final", "revision"]
    assert len([r for r in rows(db_session, setup, kind=FIDELITY)]) == 2
    critique = [c for c in gen.calls if c["role"] == "language_final_writer" and c["user"]["language"] == "de"][1]["user"]["critique"]
    assert critique["items"][0]["instruction"] == "return to the English" and critique["items"][0]["kind"] == "changed_fact"


def test_a_difference_in_meaning_that_survives_the_revision_keeps_the_language_open(setup, db_session):
    gen = FakeGen()
    gen.fidelity_items = [[fid("high")], [fid("high")]]
    out = run(gen, setup, ("en", "ar"))
    arabic = out["languages"][1]
    assert arabic["status"] == "open" and arabic["open_issues"] == 1 and out["languages"][0]["status"] == "done"
    final = [f for f in rows(db_session, setup, kind=FINAL) if f.language == "ar"][-1]
    assert final.approved_at is None and final.status == "open_issues"
    assert gen.count("language_final_writer") == 1 + 2                              # English once; Arabic twice, never three


def test_a_medium_difference_goes_into_the_revision_only_when_a_blocking_item_starts_it(setup, db_session):
    gen = FakeGen()
    gen.fidelity_items = [[fid("medium")]]
    out = run(gen, setup, ("en", "fa"))
    assert out["languages"][1]["status"] == "done"
    assert len([f for f in rows(db_session, setup, kind=FINAL) if f.language == "fa"]) == 1     # no revision for a medium item
    notes = json.loads([f for f in rows(db_session, setup, kind=FINAL) if f.language == "fa"][0].critic_notes)
    assert notes["checks"]["check_counts"] == {"medium": 1}


def test_an_invalid_fidelity_answer_is_asked_once_more_and_then_fails_the_check(setup, db_session):
    class Invalid(FakeGen):
        async def generate_structured(self, role, system, user, images=None, **k):
            task = json.loads(user)
            if role == "english_evidence_auditor" and "story" in task and "english_master" in task:
                self.calls.append({"role": role, "system": system, "user": task, **k})
                return {"verdict": "maybe"}, self._result("{}", role)
            return await super().generate_structured(role, system, user, images, **k)

    gen = Invalid()
    out = run(gen, setup, ("en", "fa"))
    assert out["languages"][1]["status"] == "failed" and "fidelity answer is not valid" in out["languages"][1]["error"]
    assert len([c for c in gen.calls if c["role"] == "english_evidence_auditor" and "story" in c["user"]]) == 2


# ------------------------------------------------------------------------------ out of date when the English changes
def test_the_case_state_shows_a_note_once_evidence_changed_after_the_plan_was_made(setup, db_session):
    run(FakeGen(), setup)
    draft = rows(db_session, setup, kind="draft")[0]

    db_session.add(Fact(case_id=setup.id, claim="A fact found after the plan was made.",
                        category="context", confidence=0.9))
    db_session.commit()

    note = case_state(db_session, setup.id)["draft"]["plan_note"]
    assert note == "New facts available since this plan was made; the plan was kept as is."
    # the plan itself (and the draft it belongs to) is unchanged — not silently replaced
    assert rows(db_session, setup, kind="draft")[0].id == draft.id


def test_a_new_english_final_makes_the_dependent_language_versions_out_of_date(setup, db_session):
    run(FakeGen(), setup, ("en", "fa"))
    state = case_state(db_session, setup.id)
    fa = next(b for b in state["languages"] if b["language"] == "fa")
    assert fa["stale"] is False and fa["approved"] is True and fa["derived_from"]["kind"] == "final"
    assert any(f.language == "en" for f in rows(db_session, setup, kind=FINAL))
    newer = StoryVersion(case_id=setup.id, version=999, kind=FINAL, language="en", narrative_angle="{}",
                         story_text="[[ACT:act1]]\n\nA new English text.", status="ready", approved_by="automatic",
                         approved_at=utc_now(),
                         narrative_structure=json.dumps({"pipeline": {"derived_from": {"id": 1, "kind": "draft"}}}),
                         critic_notes="{}", text_hash="new")
    db_session.add(newer)
    db_session.commit()
    fa = next(b for b in case_state(db_session, setup.id)["languages"] if b["language"] == "fa")
    assert fa["stale"] is True and fa["approved"] is False and str(newer.id) in fa["stale_reason"]


def test_an_edited_english_text_under_the_same_id_is_stale_by_its_hash(setup, db_session):
    run(FakeGen(), setup, ("en", "de"))
    english = next(f for f in rows(db_session, setup, kind=FINAL) if f.language == "en")
    english.text_hash = "edited"
    db_session.commit()
    de = next(b for b in case_state(db_session, setup.id)["languages"] if b["language"] == "de")
    assert de["stale"] is True and "has changed" in de["stale_reason"]


def test_a_changed_english_base_means_new_stage_rows_for_the_language_not_a_reuse(setup, db_session):
    run(FakeGen(), setup, ("en", "fa"))
    store = StageStore(db_session)
    material = asyncio.run(_pipeline(FakeGen())._material(db_session, db_session.get(Case, setup.id)))
    english = store.approved_english_final(material)
    assert english is not None
    english.approved_at = None                                       # no longer an approved base
    db_session.commit()
    assert store.approved_english_final(material) is None


# ------------------------------------------------------------------------------ the correction of confirmed findings
class Confirms(FakeGen):
    """The audit finds `findings` in the delivered English; the fix decides as `decisions` says. The English
    final has words of its own (enfinal0 enfinal1 ...) so that an edit can point at one place."""

    def __init__(self, findings, decisions):
        super().__init__()
        self.audit_items = [[], list(findings)]
        self.decisions = decisions
        self.fix_calls = []

    async def generate_text(self, role, system, user, images=None, **k):
        task = json.loads(user)
        if role == "language_final_writer" and task.get("language") == "en":
            self.calls.append({"role": role, "system": system, "user": task, "raw": user, **k})
            count = task["target_words_min"] // len(ACTS) + 10
            text = "\n\n".join(f"[[ACT:{a}]]\n\n" + " ".join(f"en{a}final{i}" for i in range(count)) for a in ACTS)
            return self._result(text, role)
        return await super().generate_text(role, system, user, images, **k)

    def fix_decisions(self, task):
        self.fix_calls.append(task)
        return self.decisions(task)


def edit_enxword(task):
    return [{"finding_id": 1, "decision": "confirmed", "reason": "the evidence says otherwise",
             "evidence_ids": ["F001"], "edits": [{"act": "act1", "old": "enact1final0 enact1final1", "new": "enact1final0"}]}]


def test_a_confirmed_finding_is_corrected_by_the_smallest_edit_and_the_text_is_reviewed_again(setup, db_session):
    gen = Confirms([finding("high")], edit_enxword)
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done"
    finals = rows(db_session, setup, kind=FINAL)
    assert [json.loads(f.narrative_structure)["pipeline"].get("stage") for f in finals] == ["final", "audit_fix"]
    original, fixed = finals
    assert fixed.approved_by == "automatic" and original.approved_at is None
    assert fixed.story_text == original.story_text.replace("enact1final0 enact1final1", "enact1final0", 1)
    assert json.loads(fixed.critic_notes)["audit_fix"]["applied"] == 1
    assert json.loads(original.critic_notes)["audit_fix"]["counts"] == {"confirmed": 1}
    assert len(rows(db_session, setup, kind=REVIEW)) == 2              # the corrected text was reviewed
    (call,) = gen.fix_calls
    assert list(call) == ["findings", "evidence", "paragraphs"] and call["findings"][0]["id"] == 1
    assert {"facts", "contradictions", "timeline"} == set(call["evidence"])
    assert any("enact1final0 enact1final1" in p["text"] for p in call["paragraphs"])


def test_after_a_correction_only_the_changed_acts_are_audited_again(setup, db_session, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.evidence_audit, "acts_per_call", 1)
    gen = Confirms([finding("high")], edit_enxword)
    gen.audit_items = [[], [], [finding("high")], []]                  # draft: 2 chunks; final: act1 finds one, act2 none
    run(gen, setup)
    audits = [c for c in gen.calls if c["role"] == "english_evidence_auditor" and "chunk" in c["user"]]
    assert len(audits) == 2 + 2 + 1                                    # draft, final, and act1 alone after the edit
    assert [a["act_id"] for a in audits[-1]["user"]["chunk"]["acts"]] == ["act1"]


def test_a_false_alarm_and_an_unclear_finding_change_nothing_and_block_nothing(setup, db_session):
    def decide(task):
        return [{"finding_id": f["id"], "decision": "unclear" if f["id"] == 1 else "false_alarm", "reason": "r",
                 "evidence_ids": [], "edits": []} for f in task["findings"]]

    findings = [finding("high"), {**finding("medium"), "id": 2}]
    out = run(Confirms(findings, decide), setup)
    assert out["languages"][0]["status"] == "done"
    (final,) = rows(db_session, setup, kind=FINAL)
    assert json.loads(final.critic_notes)["audit_fix"]["counts"] == {"unclear": 1, "false_alarm": 1}
    assert final.approved_by == "automatic"


def test_fix_rounds_so_far_counts_every_audit_fix_version_across_chains(setup, db_session):
    """Case 15: 5 manual /revise and /improve rounds on English never converged, each a fresh
    "revision"-stage chain from the latest final — counting only the current chain would never see
    the cap. The count is per case+language, across every chain."""
    gen = Confirms([finding("high")], edit_enxword)
    run(gen, setup)
    pipeline = _pipeline(gen)
    assert pipeline._fix_rounds_so_far(db_session, setup.id, "en") == 1
    other = StoryVersion(case_id=setup.id, version=99, kind=FINAL, language="en", narrative_angle="{}",
                         story_text="[[ACT:act1]]\n\nx", status="ready",
                         narrative_structure=json.dumps({"pipeline": {"stage": "audit_fix"}}),
                         critic_notes="{}", text_hash="other-chain")
    db_session.add(other)
    db_session.commit()
    assert pipeline._fix_rounds_so_far(db_session, setup.id, "en") == 2
    assert pipeline._fix_rounds_so_far(db_session, setup.id, "de") == 0


def test_after_max_fix_rounds_a_confirmed_finding_is_removed_by_code_not_asked_to_a_model_again(
        setup, db_session, monkeypatch):
    """Case 15: each model fixer round introduced new unsupported claims while fixing old ones —
    6 rounds, never converged. After audit_fix.max_fix_rounds, the next confirmed finding's flagged
    quote is deleted deterministically (no "language_audit_fix" call), then re-audited once."""
    monkeypatch.setattr(ai_config.language_pipeline.audit_fix, "max_fix_rounds", 1)
    gen = Confirms([finding("high")], edit_enxword)
    run(gen, setup)                                                # round 1: a real audit_fix version
    (original, fixed) = rows(db_session, setup, kind=FINAL)
    material = asyncio.run(_pipeline(gen)._material(db_session, db_session.get(Case, setup.id)))
    store = StageStore(db_session)
    rewrite_row = rows(db_session, setup, kind=REWRITE)[0]
    inspection_row = rows(db_session, setup, kind=INSPECTION)[0]
    base = db_session.get(StoryVersion, fixed.draft_version_id)

    def fixer_calls():
        return sum(1 for c in gen.calls if c["role"] == "english_evidence_auditor" and "findings" in c["user"])

    pipeline = _pipeline(gen)
    span = final_range("en", 60.0)
    second_finding = finding("high", quote="enact2final0 enact2final1")
    report = {"status": "ok", "items": [second_finding]}
    calls_before = fixer_calls()
    row, unresolved = asyncio.run(pipeline._deterministic_fix(
        db_session, db_session.get(Case, setup.id), store, material, base, rewrite_row, inspection_row,
        fixed, "en", span, 60.0, report, [second_finding],
        pipeline.stages._sections(fixed.story_text, material)[0]))
    assert fixer_calls() == calls_before                               # no new model call for the fix itself
    assert "enact2final0 enact2final1" not in row.story_text
    assert json.loads(row.narrative_structure)["pipeline"]["stage"] == "deterministic_fix"
    assert json.loads(row.critic_notes)["deterministic_fix"]["removed"][0]["quote"] == second_finding["quote"]
    assert unresolved == []                                          # the re-audit found nothing new


def test_a_removal_that_would_empty_out_a_whole_act_is_never_applied(setup, db_session, monkeypatch):
    """Removing a quote must never merge or delete act boundaries: a finding whose quote is an
    entire act's content would delete that act outright if applied — it is left for the next
    report instead, exactly like a quote the text has already moved past."""
    monkeypatch.setattr(ai_config.language_pipeline.audit_fix, "max_fix_rounds", 1)
    gen = Confirms([finding("high")], edit_enxword)
    run(gen, setup)
    (original, fixed) = rows(db_session, setup, kind=FINAL)
    material = asyncio.run(_pipeline(gen)._material(db_session, db_session.get(Case, setup.id)))
    store = StageStore(db_session)
    rewrite_row = rows(db_session, setup, kind=REWRITE)[0]
    inspection_row = rows(db_session, setup, kind=INSPECTION)[0]
    base = db_session.get(StoryVersion, fixed.draft_version_id)

    from app.agents.story import _parse_sections
    act2_body = next(s["text"] for s in _parse_sections(fixed.story_text) if s["id"] == "act2")
    whole_act_finding = finding("high", quote=act2_body)

    pipeline = _pipeline(gen)
    span = final_range("en", 60.0)
    report = {"status": "ok", "items": [whole_act_finding]}
    row, _ = asyncio.run(pipeline._deterministic_fix(
        db_session, db_session.get(Case, setup.id), store, material, base, rewrite_row, inspection_row,
        fixed, "en", span, 60.0, report, [whole_act_finding],
        pipeline.stages._sections(fixed.story_text, material)[0]))

    assert "act2" in [s["id"] for s in _parse_sections(row.story_text)]
    fix_notes = json.loads(row.critic_notes)["deterministic_fix"]
    assert fix_notes["removed"] == [] and fix_notes["no_quote_match"][0]["quote"] == act2_body


def test_a_deterministic_fix_that_fails_its_own_gates_is_never_saved(setup, db_session, monkeypatch):
    """The deterministic fix must run the same gates on its result before saving a new version and
    never leave a failed version as the latest (case 15 v238: saved anyway as "needs_revision",
    silently shadowing v235, the chain's last good version, as the latest final)."""
    monkeypatch.setattr(ai_config.language_pipeline.audit_fix, "max_fix_rounds", 1)
    gen = Confirms([finding("high")], edit_enxword)
    run(gen, setup)
    (original, fixed) = rows(db_session, setup, kind=FINAL)
    material = asyncio.run(_pipeline(gen)._material(db_session, db_session.get(Case, setup.id)))
    store = StageStore(db_session)
    rewrite_row = rows(db_session, setup, kind=REWRITE)[0]
    inspection_row = rows(db_session, setup, kind=INSPECTION)[0]
    base = db_session.get(StoryVersion, fixed.draft_version_id)

    from app.language_pipeline.gates import GateResult

    pipeline = _pipeline(gen)

    async def always_fails(*a, **k):
        return GateResult(failures=[{"gate": "length", "problem": "too short", "passages": []}])

    monkeypatch.setattr(pipeline.gates, "run", always_fails)
    span = final_range("en", 60.0)
    second_finding = finding("high", quote="enact2final0 enact2final1")
    report = {"status": "ok", "items": [second_finding]}
    row, unresolved = asyncio.run(pipeline._deterministic_fix(
        db_session, db_session.get(Case, setup.id), store, material, base, rewrite_row, inspection_row,
        fixed, "en", span, 60.0, report, [second_finding],
        pipeline.stages._sections(fixed.story_text, material)[0]))

    assert row.id == fixed.id                                          # the previous version stands
    assert len(rows(db_session, setup, kind=FINAL)) == 2                # no broken version was saved
    assert unresolved[0]["kind"] == "gates_failed" and "length" in unresolved[0]["problem"]


def test_a_confirmed_finding_whose_edit_cannot_be_applied_keeps_the_text_open(setup, db_session):
    def decide(task):
        return [{"finding_id": 1, "decision": "confirmed", "reason": "r", "evidence_ids": [],
                 "edits": [{"act": "act1", "old": "wording that is not in the text", "new": ""}]}]

    out = run(Confirms([finding("high")], decide), setup)
    assert out["languages"][0]["status"] == "open"
    (final,) = rows(db_session, setup, kind=FINAL)
    fix = json.loads(final.critic_notes)["audit_fix"]
    assert fix["applied"] == 0 and fix["skipped"] == 1 and final.approved_at is None


def test_a_confirmed_medium_finding_is_edited_but_does_not_block_when_it_cannot_be_applied(setup, db_session):
    def decide(task):
        return [{"finding_id": 1, "decision": "confirmed", "reason": "r", "evidence_ids": [],
                 "edits": [{"act": "act1", "old": "not in the text", "new": ""}]}]

    out = run(Confirms([finding("medium")], decide), setup)
    assert out["languages"][0]["status"] == "done"


def test_an_edit_applies_only_where_its_wording_occurs_exactly_once():
    sections = [{"id": "a", "text": "One sentence. Another sentence.\n\nOne sentence again."}]
    decisions = [FixDecision(finding_id=1, decision="confirmed", edits=[
        FixEdit(act="a", old="One sentence", new="X"),                    # twice: skipped
        FixEdit(act="a", old="Another sentence.", new=""),                # once: removed
        FixEdit(act="zz", old="y", new="")])]                             # unknown act: skipped
    new, applied, skipped = AuditFix.apply(sections, decisions)
    assert new[0]["text"] == "One sentence.\n\nOne sentence again."
    assert len(applied) == 1 and [s["reason"] for s in skipped] == ["wording not found exactly once", "act unknown"]


def test_only_a_confirmed_finding_may_carry_edits_and_every_finding_has_one_decision():
    with pytest.raises(ValueError, match="only a confirmed"):
        FixDecision(finding_id=1, decision="unclear", edits=[FixEdit(act="a", old="x")])
    with pytest.raises(ValueError, match="exactly one"):
        FixAnswer(decisions=[FixDecision(finding_id=1, decision="unclear"), FixDecision(finding_id=1, decision="unclear")])


def test_a_paragraph_repeats_a_finding_when_it_holds_the_quote_or_most_of_its_word_sequences():
    quote = "the defense elicited testimony about a thick pile of ash"
    assert carries(quote, "Then the defense elicited testimony about a thick pile of ash, they said.", 0.5)
    assert carries(quote, "The defense elicited testimony about a very thick pile of ash.", 0.5)
    assert not carries(quote, "Lizzie walked out of the courthouse.", 0.5)
    assert not carries("", "anything", 0.5)


def test_the_findings_sent_are_the_configured_severities_most_severe_first_and_capped(monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.audit_fix, "max_findings", 2)
    report = {"items": [{**finding("low"), "id": 1}, {**finding("medium"), "id": 2}, {**finding("high"), "id": 3},
                        {**finding("high"), "id": 4}]}
    assert [f["id"] for f in AuditFix.findings(report)] == [3, 4]


# ------------------------------------------------------------------------------ which items block, which revise
def test_the_policy_blocks_on_high_and_revises_on_high_and_medium():
    items = [{"severity": "high", "problem": "a", "instruction": "fix a"},
             {"severity": "medium", "problem": "b", "correction": "fix b"}, {"severity": "low", "problem": "c"}]
    assert [i["severity"] for i in review.blocking_items(items, [])] == ["high"]
    critique = review.critique_from(items[:1], items[1:])
    assert [(i["severity"], i["instruction"]) for i in critique["items"]] == [("high", "fix a"), ("medium", "fix b")]
    assert review.open_issues(items, []) == [{**items[0], "source": "review"}]
    assert review.counts(items) == {"high": 1, "medium": 1, "low": 1}


# ------------------------------------------------------------------------------ a revised plan keeps its draft
def _plan(db_session, case):
    return db_session.query(StoryPlan).filter_by(case_id=case.id).order_by(StoryPlan.id).first()


def _revise_plan(db_session, case, base, **change):
    data = json.loads(base.plan_json)
    for act in data["acts"]:
        act.update({k: v(act) if callable(v) else v for k, v in change.items()})
    base.status = "superseded"
    row = StoryPlan(case_id=case.id, version=base.version + 1, language=base.language, target_minutes=base.target_minutes,
                    status="approved", plan_json=json.dumps(data), validation_json="{}",
                    evidence_fingerprint=base.evidence_fingerprint, origin="edit", supersedes_id=base.id)
    db_session.add(row)
    db_session.commit()
    return row


def test_a_draft_stays_the_draft_of_an_edited_plan_but_not_of_a_plan_with_other_evidence(setup, db_session):
    run(FakeGen(), setup)
    base = _plan(db_session, setup)
    store = StageStore(db_session)
    edited = _revise_plan(db_session, setup, base, purpose="a sharper purpose", title="Another title")
    assert store.plan_still_fits(base.id, edited.id) is True and store.plan_still_fits(edited.id, edited.id) is True
    again = _revise_plan(db_session, setup, edited, key_beats=["one new beat"])
    assert store.plan_still_fits(base.id, again.id) is True                      # two edits in a row
    moved = _revise_plan(db_session, setup, again, evidence_ids=["F777"])
    assert store.plan_still_fits(base.id, moved.id) is False                     # the evidence assignment changed
    assert store.plan_still_fits(again.id, moved.id) is False
    redone = _revise_plan(db_session, setup, edited, purpose="p")
    redone.origin = "director"
    db_session.commit()
    assert store.plan_still_fits(base.id, redone.id) is False                    # a director's new plan is another plan


# ------------------------------------------------------------------------------ the plan edit path
def test_a_plan_edit_can_change_loops_and_plan_level_fields_through_the_previewed_path(setup, db_session):
    from app.services.story_plan_service import StoryPlanError, StoryPlanService

    run(FakeGen(), setup)
    base = _plan(db_session, setup)
    base.status = "draft"                                         # an edit replaces a plan that is not final yet
    db_session.commit()
    service = StoryPlanService(db_session)
    beats = [{"id": "act1", "purpose": "a clearer purpose", "resolved_loops": ["a loop answered here"]}]
    changes = {"ending_strategy": "End on a plain image.", "reveal_map": ["C001: act 1 only"]}
    preview = service.preview(base, "edit", beats=beats, changes=changes)
    assert preview["plan_changes"] == ["ending_strategy", "reveal_map"]
    with pytest.raises(StoryPlanError, match="changed since the preview"):
        service.edit(base, beats, preview["token"], {"ending_strategy": "other"})            # the token covers the changes
    edited = service.edit(base, beats, preview["token"], changes)
    data = json.loads(edited.plan_json)
    assert edited.origin == "edit" and edited.supersedes_id == base.id
    assert data["ending_strategy"] == "End on a plain image." and data["acts"][0]["resolved_loops"] == ["a loop answered here"]
    db_session.refresh(base)
    assert base.status == "superseded"                           # the earlier plan stays as history
    bad = service.preview(edited, "edit", beats=[], changes={"title": "x"})
    with pytest.raises(StoryPlanError, match="cannot edit plan fields"):
        service.edit(edited, [], bad["token"], {"title": "x"})


# ------------------------------------------------------------------------------ a new attempt
def test_a_new_attempt_sets_a_failed_chain_aside_and_writes_a_new_one_never_an_approved_one(setup, db_session):
    gen = FakeGen()
    gen.review_severity = "high"
    first = run(gen, setup)
    assert first["languages"][0]["status"] == "open"
    old_ids = {r.id for r in rows(db_session, setup) if r.kind in ("rewrite", "inspection", "final", "review")}
    again = FakeGen()
    out = asyncio.run(_pipeline(again).run_case(setup.id, ["en"], 60, new_attempt=True))
    assert out["languages"][0]["status"] == "done"                 # the second chain is written afresh and passes
    retired = [r for r in rows(db_session, setup) if r.id in old_ids]
    assert retired and all(r.status == "retired" for r in retired)
    assert json.loads(retired[0].critic_notes)["retired"]["reason"] == "a new attempt was requested"
    assert again.count("language_rewrite_writer") == 1             # a new rewrite, not a reuse
    final = next(r for r in rows(db_session, setup, kind=FINAL) if r.status == "ready")
    assert final.approved_at is not None
    # an approved chain cannot be set aside by a later request
    with pytest.raises(ValueError, match="approved chain"):
        StageStore(db_session).retire_attempt(setup.id, "en", final.draft_version_id, "again")
    assert next(b for b in case_state(db_session, setup.id)["languages"] if b["language"] == "en")["approved"] is True


# ------------------------------------------------------------------------------ the explicit revision round
def test_the_revision_round_asked_for_works_on_the_review_items_once_and_checks_the_result(setup, db_session):
    gen = FakeGen()
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done"
    delivered = out["languages"][0]["final_version_id"]
    revised = asyncio.run(_pipeline(gen).revise_final(setup.id, "en", 60))
    assert revised.status == "ready" and revised.approved_by == "automatic" and revised.id != delivered
    assert json.loads(revised.narrative_structure)["pipeline"]["stage"] == "revision"
    call = [c for c in gen.calls if c["role"] == "language_final_writer"][-1]
    assert call["user"]["last_version"] == db_session.get(StoryVersion, delivered).story_text
    assert [i["severity"] for i in call["user"]["critique"]["items"]] == ["medium"]            # the review's item
    assert len(rows(db_session, setup, kind=REVIEW)) == 2                                     # the revision was reviewed
    with pytest.raises(StageFailed, match="already a revision"):
        asyncio.run(_pipeline(gen).revise_final(setup.id, "en", 60))                           # once per version


def test_a_revision_that_falls_below_the_floor_may_use_the_chains_one_length_correction(setup, db_session):
    class Shrinks(FakeGen):
        async def generate_text(self, role, system, user, images=None, **k):
            task = json.loads(user)
            if role == "language_final_writer" and "acts_to_expand" not in task and task.get("critique", {}).get("summary", "").startswith("Revision round"):
                self.calls.append({"role": role, "system": system, "user": task, **k})
                return self._result("\n\n".join(f"[[ACT:{a}]]\n\n" + " ".join(["short"] * 40) for a in ACTS), role)
            if "acts_to_expand" in task:
                self.calls.append({"role": role, "system": system, "user": task, **k})
                return self._result("\n\n".join(f"[[ACT:{a['act_id']}]]\n\n" + " ".join(f"new{a['act_id']}w{i}" for i in range(300))
                                               for a in task["acts_to_expand"]), role)
            return await super().generate_text(role, system, user, images, **k)

    gen = Shrinks()
    run(gen, setup)
    revised = asyncio.run(_pipeline(gen).revise_final(setup.id, "en", 60))
    assert len([c for c in gen.calls if "acts_to_expand" in c["user"]]) == 1             # the correction ran once
    assert json.loads(revised.narrative_structure)["pipeline"]["stage"] == "length_correction"
    assert revised.status == "ready"


def test_the_chains_one_length_correction_is_not_used_a_second_time_after_a_revision(setup, db_session):
    class AlwaysShort(FakeGen):
        async def generate_text(self, role, system, user, images=None, **k):
            task = json.loads(user)
            if role == "language_final_writer" and "acts_to_expand" not in task:
                self.calls.append({"role": role, "system": system, "user": task, **k})
                return self._result("\n\n".join(f"[[ACT:{a}]]\n\n" + " ".join(["short"] * 40) for a in ACTS), role)
            if "acts_to_expand" in task:
                self.calls.append({"role": role, "system": system, "user": task, **k})
                return self._result("\n\n".join(f"[[ACT:{a['act_id']}]]\n\n" + " ".join(["more"] * 41) for a in task["acts_to_expand"]), role)
            return await super().generate_text(role, system, user, images, **k)

    gen = AlwaysShort()
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "failed"
    assert len([c for c in gen.calls if "acts_to_expand" in c["user"]]) == 1

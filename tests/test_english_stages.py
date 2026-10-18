# ruff: noqa: F811  (`setup` is the flow tests' fixture, imported)
"""The English-first plan. The draft gets an automatic approval record that no audit result gates; English
is re-told from it and, once its delivered text is checked (the inspector, the evidence audit and the
correction of confirmed findings), approved; every other language is re-told from that approved English
final; no language starts before the text it is re-told from is approved; the rows of the retired
fact-check loop stay as history."""

import asyncio
import json
from pathlib import Path

from test_evidence_audit import Broken
from test_language_pipeline_flow import FakeGen, _pipeline, setup  # noqa: F401

from app.core.ai_config import ai_config
from app.db.models import Case, StoryVersion
from app.language_pipeline.store import RETIRED, RETIRED_KINDS, StageStore
from app.providers.generation.base import GenerationError

ROOT = Path(__file__).resolve().parents[1]


def run(gen, case, languages=("en",)):
    return asyncio.run(_pipeline(gen).run_case(case.id, list(languages), 60))


# ----------------------------------------------------------------------------- the draft's approval
def test_the_draft_gets_an_automatic_approval_record_with_time_draft_version_and_cost(setup, db_session):
    out = run(FakeGen(), setup)
    db_session.expire_all()
    draft = db_session.get(StoryVersion, out["draft_version_id"])
    approval = json.loads(draft.critic_notes)["approval"]
    assert draft.approved_by == "automatic" and draft.approved_at is not None
    assert approval["by"] == "automatic" and approval["draft_version_id"] == draft.id
    assert approval["cost_eur"] == draft.cost_eur and approval["at"]


def test_a_failed_audit_does_not_gate_the_drafts_approval_but_the_english_final_is_not_approved_unchecked(setup, db_session):
    gen = Broken(GenerationError("forbidden", "APIMaster permission/quota failure (403)."))
    out = run(gen, setup, ("en", "fa"))
    db_session.expire_all()
    draft = db_session.get(StoryVersion, out["draft_version_id"])
    notes = json.loads(draft.critic_notes)
    assert notes["evidence_audit"]["status"] == "failed" and notes["approval"]["evidence_audit"] == "failed"
    assert draft.approved_by == "automatic"
    # the factual check of the delivered English text could not be made: the text stays open, nothing derives from it
    english, persian = out["languages"]
    assert english["status"] == "open" and english["open_issues"] == 1
    assert persian["status"] == "failed" and "no approved en final text" in persian["error"]
    final = db_session.get(StoryVersion, english["final_version_id"])
    assert final.status == "open_issues" and final.approved_at is None


def test_the_approval_is_written_once_and_a_second_run_changes_nothing(setup, db_session):
    first = run(FakeGen(), setup)
    db_session.expire_all()
    at = db_session.get(StoryVersion, first["draft_version_id"]).approved_at
    again = FakeGen()
    run(again, setup)
    db_session.expire_all()
    assert db_session.get(StoryVersion, first["draft_version_id"]).approved_at == at
    assert again.count("language_draft_writer") == 0 and again.count("language_rewrite_writer") == 0


def test_no_language_starts_before_the_text_it_is_re_told_from_is_approved(setup, db_session):
    out = run(FakeGen(), setup, ("en",))
    db_session.expire_all()
    draft = db_session.get(StoryVersion, out["draft_version_id"])
    english = db_session.get(StoryVersion, out["languages"][0]["final_version_id"])
    saved = (draft.approved_by, draft.approved_at)
    draft.approved_by, draft.approved_at = None, None          # an approval that was never written
    db_session.commit()
    gen = FakeGen()
    pipeline = _pipeline(gen)
    material = asyncio.run(pipeline._material(db_session, db_session.get(Case, setup.id)))
    outcome = asyncio.run(pipeline.run_language(setup.id, "en", material, draft.id, 60))
    assert outcome.status == "failed" and "no approval record" in outcome.error
    english.approved_by, english.approved_at = None, None      # nor is the English final approved
    english.status = "open_issues"
    db_session.commit()
    outcome = asyncio.run(pipeline.run_language(setup.id, "fa", material, english.id, 60))
    assert outcome.status == "failed" and "no approval record" in outcome.error
    assert gen.calls == []                                     # not one model call was made
    draft.approved_by, draft.approved_at = saved
    StageStore(db_session).approve_automatically(english, {})
    db_session.query(StoryVersion).filter_by(id=english.id).update({"status": "ready"})
    db_session.commit()
    outcome = asyncio.run(pipeline.run_language(setup.id, "fa", material, english.id, 60))
    assert outcome.status == "done" and gen.count("language_rewrite_writer") == 1


def test_the_code_writes_the_approval_before_it_starts_any_language():
    source = (ROOT / "app/language_pipeline/orchestrator.py").read_text()
    run_case = source[source.index("async def run_case("):source.index("async def run_languages(")]
    assert run_case.index("ensure_audit") < run_case.index("ensure_draft_approved") < run_case.index("run_language(")
    assert run_case.index("run_language(") < run_case.index("run_languages(")       # English first, then the others


# ----------------------------------------------------------------------------- the draft's identity
def test_a_draft_written_with_another_draft_prompt_is_history_and_a_new_draft_is_written(setup, db_session):
    first = run(FakeGen(), setup)
    db_session.expire_all()
    old = db_session.get(StoryVersion, first["draft_version_id"])
    old.writer_prompt_sha256 = "0" * 64                          # written by an earlier version of the prompt
    db_session.commit()
    gen = FakeGen()
    second = run(gen, setup)
    assert second["draft_version_id"] != old.id and not second["draft_reused"]
    assert gen.count("language_draft_writer") == 2
    db_session.expire_all()
    assert db_session.get(StoryVersion, old.id).status == "ready"      # kept as it was, not overwritten
    rows = db_session.query(StoryVersion).filter(
        StoryVersion.draft_version_id == second["draft_version_id"], StoryVersion.kind == "rewrite").all()
    assert len(rows) == 1                                              # the new draft is the source, not the old one


# ----------------------------------------------------------------------------- the retired loop
def _loop_row(db_session, case, kind, status="stage_output", draft_id=None):
    row = StoryVersion(case_id=case.id, version=900 + len(kind), kind=kind, language="en", narrative_angle="{}",
                       story_text="x", status=status, critic_notes=json.dumps({"summary": "old"}),
                       narrative_structure=json.dumps({"pipeline": {"round": 1}}), draft_version_id=draft_id)
    db_session.add(row)
    db_session.commit()
    return row


def test_the_rows_of_the_retired_loop_are_set_aside_through_the_store_with_a_reason(setup, db_session):
    rows = [_loop_row(db_session, setup, "master_inspect"), _loop_row(db_session, setup, "master_revise", "rejected"),
            _loop_row(db_session, setup, "english_master", "stopped")]
    other = _loop_row(db_session, setup, "rewrite")
    retired = StageStore(db_session).retire_loop_rows(setup.id, "The fact-check loop was retired.")
    assert [r.id for r in retired] == [r.id for r in rows]
    db_session.expire_all()
    for row in rows:
        notes = json.loads(db_session.get(StoryVersion, row.id).critic_notes)
        assert db_session.get(StoryVersion, row.id).status == RETIRED
        assert notes["retired"]["reason"] == "The fact-check loop was retired." and notes["retired"]["at"]
        assert notes["summary"] == "old"                                # nothing else of the row changed
    assert db_session.get(StoryVersion, other.id).status == "stage_output"
    assert [json.loads(db_session.get(StoryVersion, r.id).critic_notes)["retired"]["was"] for r in rows] == [
        "stage_output", "rejected", "stopped"]
    assert StageStore(db_session).retire_loop_rows(setup.id, "again") == []     # a retired row is not retired twice


def test_the_state_labels_the_retired_rows_and_never_uses_them_as_a_source(setup, db_session):
    from app.language_pipeline.state import case_state

    out = run(FakeGen(), setup)
    _loop_row(db_session, setup, "master_inspect")
    _loop_row(db_session, setup, "english_master", "stopped")
    StageStore(db_session).retire_loop_rows(setup.id, "retired method")
    db_session.expire_all()
    state = case_state(db_session, setup.id)
    assert [(r["kind"], r["status"], r["retired"]["reason"]) for r in state["retired_method"]] == [
        ("master_inspect", "retired", "retired method"), ("english_master", "retired", "retired method")]
    assert state["draft"]["id"] == out["draft_version_id"] and state["draft"]["approval"]["by"] == "automatic"
    assert "english_master" not in state and set(RETIRED_KINDS) == {"master_inspect", "master_revise", "english_master"}


# ----------------------------------------------------------------------------- no fact check in English
def test_no_agent_role_or_code_path_checks_facts_after_the_draft_except_the_audit():
    assert not {"language.fact_check", "language.revise_english", "story.grounding", "story.consistency"} & set(ai_config.agents)
    for role in ("english_fact_inspector", "english_reviser"):
        assert role not in ai_config.generation_provider().routing and role not in ai_config.generation
    assert ai_config.agents["english.evidence_audit"].output == "json"
    forbidden = ("fact_inspector", "english_reviser", "EnglishMasterLoop", "master_stages", "fact_check(")
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text()
        assert not [word for word in forbidden if word in text], path
    for name in ("fact_inspector", "reviser"):
        assert not (ROOT / f"prompts/language_pipeline/english/{name}.md").exists()
    assert not hasattr(ai_config.language_pipeline, "english_master")


def test_the_english_chain_checks_the_delivered_text_with_the_inspector_and_the_evidence_audit(setup):
    gen = FakeGen()
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done"
    roles = [c["role"] for c in gen.calls]
    # the draft audit and the audit of the delivered final; no finding, so no audit fix call
    assert roles.count("language_draft_writer") == 2 and roles.count("english_evidence_auditor") == 2
    chain = [r for r in roles if r in ("language_rewrite_writer", "language_inspector", "language_final_writer")]
    assert chain == ["language_rewrite_writer", "language_inspector", "language_final_writer", "language_inspector"]
    assert set(roles) == {"language_draft_writer", "english_evidence_auditor", "language_rewrite_writer",
                          "language_inspector", "language_final_writer"}

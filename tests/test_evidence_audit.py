# ruff: noqa: F811  (`setup` is the flow tests' fixture, imported)
"""The one-pass evidence audit of the English draft: report only, evidence and chunk in, nothing
else, the same stable part in every call, never blocking, never changing a word."""

import asyncio
import json
import string

import pytest
from test_language_pipeline_flow import FakeGen, _pipeline, setup  # noqa: F401

from app.core.ai_config import AIConfig, ai_config, load_raw_config
from app.core.prompts import prompt
from app.db.models import StoryVersion
from app.language_pipeline import draft_checks, evidence_audit
from app.language_pipeline.evidence_audit import AuditAnswer, stable_part
from app.language_pipeline.materials import SharedMaterial
from app.providers.generation.base import GenerationError, GenerationResult


def finding(number, severity="high", kind="wrong_detail", act="act1", **extra):
    return {"id": number, "severity": severity, "kind": kind, "act": act, "quote": "dword dword",
            "claim": "c", "evidence_ids": ["F001"], "evidence": "e", "problem": "p",
            "correction": "remove", **extra}


def run(gen, case, languages=("en",)):
    return asyncio.run(_pipeline(gen).run_case(case.id, list(languages), 60))


def draft_notes(db_session, out):
    db_session.expire_all()
    return json.loads(db_session.get(StoryVersion, out["draft_version_id"]).critic_notes)


# ----------------------------------------------------------------------------- what the auditor gets
def test_the_auditor_gets_the_evidence_and_the_chunk_and_nothing_else(setup, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.evidence_audit, "acts_per_call", 1)
    gen = FakeGen()
    run(gen, setup, ("en", "de"))
    calls = [c for c in gen.calls if c["role"] == "english_evidence_auditor" and "chunk" in c["user"] and
             c["user"]["chunk"]["acts"][0]["text"].startswith("dword")]                    # the draft's audit
    assert len(calls) == 2
    for call in calls:
        user = call["user"]
        assert list(user) == ["evidence", "key_fact_ids", "chunk"]           # stable keys first, chunk last
        assert list(user["evidence"]) == ["facts", "sources", "timeline", "contradictions", "approved_context"]
        assert set(user["evidence"]["facts"][0]) == {"id", "text", "source_ids", "excerpt", "uncertain",
                                                     "quote_status"}
        assert len(user["chunk"]["acts"]) == 1 and set(user["chunk"]["acts"][0]) == {"act_id", "text"}
        everything = json.dumps(user) + (call["prefix"] or "") + call["system"]
        for absent in ("do_not_reveal", "reveal_map", "key_beats", "ending_strategy", "central_question",
                       "english_master", "last_version", "critique", "target_words"):
            assert absent not in everything, absent
        assert call["system"].startswith("You are the factual auditor of an English true-crime documentary script")
    texts = {a["act_id"]: a["text"] for c in calls for a in c["user"]["chunk"]["acts"]}
    assert all(t.startswith("dword") for t in texts.values())                  # the English draft only


def test_the_stable_part_is_byte_identical_in_every_call_and_the_cache_key_is_shared(setup, monkeypatch):
    class Raw(FakeGen):
        async def generate_structured(self, role, system, user, images=None, **k):
            if role == "english_evidence_auditor":
                self.raw = getattr(self, "raw", []) + [(system, user, k.get("cache_key"))]
            return await super().generate_structured(role, system, user, images, **k)

    monkeypatch.setattr(ai_config.language_pipeline.evidence_audit, "acts_per_call", 1)
    gen = Raw()
    run(gen, setup)
    (s1, u1, k1), (s2, u2, k2) = [r for r in gen.raw if '"text":"dword' in r[1]]
    assert s1 == s2 and k1 == k2 and k1                                     # same system prompt, same cache key
    assert stable_part(u1) == stable_part(u2) and u1 != u2
    assert u1.startswith('{"evidence":{"facts":[') and ',"key_fact_ids":[' in stable_part(u1)
    assert u1[len(stable_part(u1)):].startswith(',"chunk":{"acts":[{"act_id":"act1"')
    assert u2[len(stable_part(u2)):].startswith(',"chunk":{"acts":[{"act_id":"act2"')


def test_calls_after_the_first_show_their_cached_tokens_in_the_ledger(setup, db_session, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.evidence_audit, "acts_per_call", 1)
    out = run(FakeGen(), setup)
    audit = draft_notes(db_session, out)["evidence_audit"]
    assert [c["usage"]["calls"] for c in audit["chunks"]] == [1, 1]
    assert audit["usage"]["tokens_cached"] > 0 and audit["usage"]["cost_eur"] > 0


# ----------------------------------------------------------------------------- report only
def test_the_audit_changes_no_text_and_makes_no_further_model_call(setup, db_session):
    gen = FakeGen()
    gen.audit_items = [[finding(1)]]
    out = run(gen, setup)
    assert gen.count("english_evidence_auditor") == 2           # the draft (2 acts, 2 per call) and the delivered final
    assert gen.count("english_reviser") == 0                                   # nothing was applied
    draft = db_session.get(StoryVersion, out["draft_version_id"])
    assert all(w.startswith("dword") or w.endswith(".") for w in draft.story_text.split())
    assert draft.status == "ready" and out["languages"][0]["status"] == "done"
    audit = json.loads(draft.critic_notes)["evidence_audit"]
    assert audit["report_only"] is True and audit["note"] == "Report only: nothing was changed."


def test_the_report_merges_the_chunks_sorted_by_severity_with_counts_and_cited_facts(setup, db_session, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.evidence_audit, "acts_per_call", 1)
    gen = FakeGen()
    gen.audit_items = [[finding(1, "low", "distortion", "act1")],
                       [finding(1, "high", "quote", "act2", evidence_ids=["F001", "F002"]),
                        finding(2, "medium", "wrong_detail", "act2")]]
    out = run(gen, setup)
    audit = draft_notes(db_session, out)["evidence_audit"]
    assert audit["status"] == "ok" and audit["verdict"] == "issues"
    assert [(i["id"], i["severity"], i["act"]) for i in audit["items"]] == [
        (1, "high", "act2"), (2, "medium", "act2"), (3, "low", "act1")]
    assert audit["counts"] == {"severity": {"high": 1, "medium": 1, "low": 1},
                               "kind": {"quote": 1, "wrong_detail": 1, "distortion": 1}}
    cited = audit["items"][0]["evidence_facts"]
    assert [f["id"] for f in cited] == ["F001", "F002"] and "Fact number" in cited[0]["text"]
    assert audit["models"] == ["fake"] and len(audit["chunks"]) == 2


def test_a_clean_audit_passes_and_says_only_what_it_checked(setup, db_session):
    out = run(FakeGen(), setup)
    audit = draft_notes(db_session, out)["evidence_audit"]
    assert audit["verdict"] == "pass" and audit["items"] == [] and audit["status"] == "ok"


def test_the_verdict_must_follow_the_items():
    with pytest.raises(ValueError, match="exactly when"):
        AuditAnswer.model_validate({"verdict": "issues", "items": [], "summary": "x"})
    with pytest.raises(ValueError, match="exactly when"):
        AuditAnswer.model_validate({"verdict": "pass", "items": [finding(1)], "summary": "x"})
    AuditAnswer.model_validate({"verdict": "issues", "items": [finding(1, evidence_ids=[12, 40])], "summary": "x"})


# ----------------------------------------------------------------------------- failures never block
class Broken(FakeGen):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def generate_structured(self, role, system, user, images=None, **k):
        if role == "english_evidence_auditor":
            self.calls.append({"role": role, "system": system, "user": json.loads(user), **k})
            if isinstance(self.error, Exception):
                raise self.error
            return self.error, self._result("{}", role)
        return await super().generate_structured(role, system, user, images, **k)


@pytest.mark.parametrize("error", [
    GenerationError("invalid_output", "completion truncated at max_tokens"),
    GenerationError("forbidden", "APIMaster permission/quota failure (403)."),
])
def test_a_failed_audit_is_recorded_as_failed_and_the_pipeline_goes_on(setup, db_session, error):
    gen = Broken(error)
    out = run(gen, setup)
    audit = draft_notes(db_session, out)["evidence_audit"]
    assert audit["status"] == "failed" and audit["verdict"] == "failed" and audit["items"] == []
    assert error.kind in audit["chunks"][0]["error"]
    assert out["languages"][0]["status"] == "open"               # the delivered text could not be checked: not approved


def test_an_invalid_answer_is_asked_again_once_then_recorded_as_failed(setup, db_session):
    gen = Broken({"verdict": "maybe", "items": [], "summary": ""})
    out = run(gen, setup)
    assert gen.count("english_evidence_auditor") == 4                            # asked, then once more: draft and final
    assert draft_notes(db_session, out)["evidence_audit"]["status"] == "failed"
    assert out["languages"][0]["status"] == "open"


def test_an_unexpected_error_in_the_audit_is_recorded_and_does_not_stop_the_pipeline(setup, db_session):
    gen = Broken(RuntimeError("bug"))
    out = run(gen, setup)
    audit = draft_notes(db_session, out)["evidence_audit"]
    assert audit["status"] == "failed" and "bug" in audit["error"] and out["languages"][0]["status"] == "open"


def test_the_audit_runs_once_and_a_failed_chunk_is_tried_again_alone(setup, db_session, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.evidence_audit, "acts_per_call", 1)

    class SecondFails(FakeGen):
        n = 0

        async def generate_structured(self, role, system, user, images=None, **k):
            if role == "english_evidence_auditor":
                self.n += 1
                if self.n == 2:
                    self.calls.append({"role": role, "system": system, "user": json.loads(user), **k})
                    raise GenerationError("invalid_output", "cut off")
            return await super().generate_structured(role, system, user, images, **k)

    first = SecondFails()
    out = run(first, setup)
    assert draft_notes(db_session, out)["evidence_audit"]["status"] == "partial"
    again = FakeGen()
    out = run(again, setup)
    assert [c["user"]["chunk"]["acts"][0]["act_id"] for c in again.calls
            if c["role"] == "english_evidence_auditor"] == ["act2"]               # only the failed chunk
    assert draft_notes(db_session, out)["evidence_audit"]["status"] == "ok"
    third = FakeGen()
    run(third, setup)
    assert third.count("english_evidence_auditor") == 0                            # once means once


def test_the_audit_can_be_switched_off_in_config(setup, db_session, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.evidence_audit, "enabled", False)
    gen = FakeGen()
    out = run(gen, setup)
    assert gen.count("english_evidence_auditor") == 0
    assert "evidence_audit" not in draft_notes(db_session, out) and "draft_checks" in draft_notes(db_session, out)


# ----------------------------------------------------------------------------- the deterministic partners
def _material(facts, sources=()):
    pack = {"facts": facts}
    return SharedMaterial(case_id=1, plan_id=1, plan={}, pack=pack, draft_pack=pack, fingerprint="x",
                          sources=tuple(sources))


def test_numbers_and_names_report_what_the_evidence_does_not_state():
    material = _material([{"id": "F001", "claim": "Abby Borden died on August 4, 1892 in Fall River.",
                           "supporting_text": "", "quote_status": None}],
                         [{"id": 1, "title": "The Fall River Herald", "type": "news"}])
    report = draft_checks.numbers_and_names(
        material, "In 1892 Abby Borden died. In 1893 Bridget Sullivan spoke to the Fall River Herald.")
    assert [f["figure"] for f in report["unsupported_figures"]] == ["1893"]
    assert "Bridget Sullivan" in report["names_not_in_evidence"] and "Abby Borden" not in report["names_not_in_evidence"]
    assert report["model_calls"] == 0 and report["blocking"] is False


def test_the_coverage_report_names_what_the_draft_does_not_tell():
    class Engine:
        async def tellings(self, material, sections):
            return {"F001": object()}

    material = _material([{"id": "F001", "claim": "a"}, {"id": "F002", "claim": "b"}])
    report = asyncio.run(draft_checks.coverage(Engine(), material, [], 2))
    assert report["told"] == 1 and report["not_told"] == ["F002"] and report["key_facts_not_told"] == ["F002"]
    assert report["model_calls"] == 0
    assert asyncio.run(draft_checks.coverage(None, material, [], 2))["skipped"]


def test_the_draft_row_carries_all_three_reports(setup, db_session):
    out = run(FakeGen(), setup)
    notes = draft_notes(db_session, out)
    assert set(notes) >= {"evidence_audit", "draft_checks"}
    assert set(notes["draft_checks"]) == {"numbers_and_names", "coverage"}
    from app.language_pipeline.state import case_state

    db_session.expire_all()
    draft = case_state(db_session, setup.id)["draft"]
    assert draft["evidence_audit"]["status"] == "ok" and draft["draft_checks"]["numbers_and_names"]["model_calls"] == 0


# ----------------------------------------------------------------------------- config, prompt, independence
def test_the_agent_the_role_and_the_independent_group_are_configured_as_specified():
    agent = ai_config.agents["english.evidence_audit"]
    assert (agent.role, agent.prompt, agent.output) == (
        "english_evidence_auditor", "language_pipeline/audit/evidence_audit", "json")
    assert ai_config.model_for("english_evidence_auditor") == "gpt-6.1-sol" or \
        ai_config.alias_for("english_evidence_auditor") == "sol"
    assert ai_config.alias_for("language_draft_writer") == "writer"        # Gemini Flash (2026-10-18)
    assert ai_config.generation_for("english_evidence_auditor").reasoning_effort == "high"
    raw = load_raw_config()
    group = next(g for g in raw["review_independence"]["groups"] if g["name"] == "english_evidence_audit")
    assert group["authors"] == ["language_draft_writer"] and group["reviewers"] == ["english_evidence_auditor"]
    assert group["strict"] is True and "same_model_allowed" not in group
    bad = json.loads(json.dumps(raw))
    routing = bad["generation_providers"]["apimaster"]["routing"]
    routing["english_evidence_auditor"] = routing["language_draft_writer"]
    with pytest.raises(ValueError, match="english_evidence_auditor"):
        AIConfig.model_validate(bad)
    cfg = ai_config.language_pipeline.evidence_audit
    assert (cfg.enabled, cfg.acts_per_call, cfg.max_items_per_call, cfg.key_fact_count) == (True, 2, 25, 40)


def test_the_prompt_carries_its_detection_markers_and_formats_cleanly():
    text = prompt("language_pipeline/audit/evidence_audit")
    assert "factual auditor" in text and "AUDIT AGAINST THE SUPPLIED MATERIAL ONLY" in text
    found = {f for _, f, _, _ in string.Formatter().parse(text) if f}
    assert found == {"max_items"}
    out = text.format(max_items=25)
    assert "at most 25 items per call" in out
    assert '{"verdict":"pass","items":[]' in out and "{max_items}" not in out and "{{" not in out


def test_the_plan_and_other_languages_never_reach_the_auditor_in_the_real_material():
    material = _material([{"id": "F001", "claim": "a", "supporting_text": "s", "source_ids": [1]}],
                         [{"id": 1, "title": "T", "type": "news"}])
    material = SharedMaterial(**{**material.__dict__, "plan": {"acts": [{"id": "act1", "do_not_reveal": ["F009"]}],
                                                              "reveal_map": {"x": 1}}})
    text = evidence_audit.message(material, [{"id": "act1", "text": "Narration."}])
    assert "do_not_reveal" not in text and "reveal_map" not in text and "F009" not in text
    assert json.loads(text)["evidence"]["sources"] == [{"id": 1, "title": "T", "type": "news"}]


def test_a_cache_key_without_a_prefix_is_sent_to_the_provider():
    from app.agents.runner import run_agent

    seen = {}

    class Gen:
        async def generate_structured(self, role, system, user, images=None, **extra):
            seen.update(extra)
            return {}, GenerationResult(text="{}", model="m", provider="p")

    asyncio.run(run_agent("english.evidence_audit", Gen(), "u", system="s", cache_key="k"))
    assert seen == {"cache_key": "k"}


# ----------------------------------------------------------------------------- the audit of a final text
def test_the_delivered_final_is_audited_with_the_same_audit_and_the_report_is_stored_with_it(setup, db_session):
    gen = FakeGen()
    gen.audit_items = [[], [finding(1, quote="enxword enxword", act="act2", severity="medium")]]
    out = run(gen, setup)
    draft_before = draft_notes(db_session, out)["evidence_audit"]
    audited = [c for c in gen.calls if c["role"] == "english_evidence_auditor" and "chunk" in c["user"]][1:]
    texts = {a["act_id"]: a["text"] for c in audited for a in c["user"]["chunk"]["acts"]}
    assert set(texts) == {"act1", "act2"} and all(t.startswith("enxword") for t in texts.values())     # the final, not the draft
    db_session.expire_all()
    final = db_session.get(StoryVersion, out["languages"][0]["final_version_id"])
    report = json.loads(final.critic_notes)["evidence_audit"]
    assert report["status"] == "ok" and report["report_only"] is True and len(report["items"]) == 1
    assert report["items"][0]["act"] == "act2" and draft_notes(db_session, out)["evidence_audit"] == draft_before
    # the fix step decided the finding (a false alarm in this fake), so the text is unchanged and approved
    assert json.loads(final.critic_notes)["audit_fix"]["counts"] == {"false_alarm": 1}
    assert final.status == "ready" and final.approved_by == "automatic"


def test_a_manual_audit_of_an_already_audited_final_calls_nothing_again(setup, db_session):
    gen = FakeGen()
    run(gen, setup)
    before = gen.count("english_evidence_auditor")
    report = asyncio.run(_pipeline(gen).audit_final(setup.id, "en"))
    assert report["status"] == "ok" and gen.count("english_evidence_auditor") == before


def test_auditing_a_language_without_a_final_fails_before_any_call(setup):
    gen = FakeGen()
    from app.language_pipeline.stages import StageFailed

    with pytest.raises(StageFailed):
        asyncio.run(_pipeline(gen).audit_final(setup.id, "en"))
    assert gen.calls == []


def test_the_auditor_reads_the_contradictions_the_writer_read_so_a_claim_from_them_is_supported():
    material = _material([{"id": "F001", "claim": "a", "supporting_text": "s", "source_ids": [1]}],
                         [{"id": 1, "title": "T", "type": "news"}])
    pack = {**material.draft_pack,
            "contradictions": [{"id": "C008", "topic": "t", "description": "the defense elicited testimony about "
                                "a pile of ash", "severity": "medium"}],
            "timeline": [{"id": "T001", "event_date": "1892-08-04", "claim": "The murders."}],
            "approved_context": [{"id": "CTX1", "source": "S", "language": "en", "summary": "context"}]}
    material = SharedMaterial(**{**material.__dict__, "draft_pack": pack})
    evidence = json.loads(evidence_audit.message(material, [{"id": "act1", "text": "Narration."}]))["evidence"]
    assert evidence["contradictions"][0]["id"] == "C008" and "pile of ash" in evidence["contradictions"][0]["description"]
    assert evidence["timeline"] == [{"id": "T001", "event_date": "1892-08-04", "claim": "The murders."}]
    assert evidence["approved_context"][0]["summary"] == "context"

"""T5/T11/T13/T14: stage order and inputs, one draft for all languages, resume without
re-billing, failure isolation, gate failures going back to stage 4 only."""

import asyncio
import itertools
import json
import uuid

import pytest

from app.db.models import Case, Fact, StoryPlan, StoryVersion
from app.language_pipeline.film_schema import FILM_SCORES
from app.language_pipeline.lengths import final_range
from app.language_pipeline.orchestrator import LanguagePipeline
from app.language_pipeline.store import DRAFT, FIDELITY, FINAL, INSPECTION, REVIEW, REWRITE
from app.providers.generation.base import GenerationResult

ACTS = ["act1", "act2"]


def _marked(words_per_act: int, tag: str) -> str:
    return "\n\n".join(f"[[ACT:{a}]]\n\n" + " ".join([f"{tag}word" for _ in range(words_per_act)]) + "."
                       for a in ACTS)


class FakeGen:
    """Records every call; answers by role. Lengths follow the config ranges."""

    def __init__(self, fail_language=None):
        self.calls = []
        self.audit_items = []           # what the evidence auditor finds, call by call (a list of items)
        self.fail_language = fail_language
        self.bad_markers_once = set()
        self.short_drafts = 0
        self.audit_items = getattr(self, "audit_items", [])
        self.fidelity_items = []          # what the meaning check finds, call by call (a list of items)
        self.review_severity = "medium"   # the severity of the one item every later inspector call returns
        self.inspections: dict = {}

    def fix_decisions(self, task):
        """What the audit fix decides; a test sets this to confirm findings and give edits."""
        return None

    scene_numbers = itertools.count(1)

    def draft_words(self, count):
        """Words no earlier scene used, so no scene retells another."""
        number = next(FakeGen.scene_numbers)
        return " ".join(f"dword{number}x{i}" for i in range(count)) + "."

    def _result(self, text, role):
        return GenerationResult(text=text, model="fake", provider="fake", input_tokens=10,
                                output_tokens=len(text.split()), total_tokens=10,
                                cached_tokens=4, reasoning_tokens=2, reasoning_effort="high",
                                cost_usd=0.01)

    async def generate_text(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        task = json.loads(user)
        self.calls.append({"role": role, "system": system, "user": task, "prefix": prefix,
                           "cache_key": cache_key})
        language = task.get("language")
        if self.fail_language == language and role == "language_rewrite_writer":
            raise RuntimeError("provider down")
        if role == "language_draft_writer":
            words = task["unit_target_words"]
            if self.short_drafts:
                self.short_drafts -= 1
                words = 20
            return self._result(self.draft_words(words), role)
        words = task.get("target_words_min") or 100
        per_act = int((words + 20) / len(ACTS))
        text = _marked(per_act, f"{language}x")
        if role == "language_rewrite_writer" and language in self.bad_markers_once:
            self.bad_markers_once.discard(language)
            text = "no markers here"
        return self._result(text, role)

    async def generate_structured(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        task = json.loads(user)
        self.calls.append({"role": role, "system": system, "user": task, "prefix": prefix,
                           "cache_key": cache_key})
        if role == "english_evidence_auditor":
            if "findings" in task:                                    # the audit fix: one decision per finding
                decisions = [{"finding_id": f["id"], "decision": "false_alarm", "reason": "supported",
                              "evidence_ids": [], "edits": []} for f in task["findings"]]
                return {"decisions": self.fix_decisions(task) or decisions, "summary": "checked"}, self._result("{}", role)
            if "english_master" in task and "story" in task:          # the meaning check of a translation
                items = self.fidelity_items.pop(0) if self.fidelity_items else []
                return ({"verdict": "issues" if items else "pass", "items": items, "summary": "compared"},
                        self._result("{}", role))
            items = self.audit_items.pop(0) if self.audit_items else []
            return ({"verdict": "issues" if items else "pass", "items": items, "summary": "checked"},
                    self._result("{}", role))
        if role == "language_inspector":
            language = task.get("language")
            self.inspections[language] = self.inspections.get(language, 0) + 1
            severity = "high" if self.inspections[language] == 1 else self.review_severity
            return ({"verdict": "revise", "scores": {k: 80 for k in FILM_SCORES}, "summary": "ok",
                     "items": [{"id": 1, "severity": severity, "kind": "spoken", "act": "act1",
                                "quote": "x", "problem": "p", "instruction": "i"}]},
                    self._result("{}", role))
        raise AssertionError(role)

    def count(self, role, language=None):
        return sum(1 for c in self.calls if c["role"] == role
                   and (language is None or c["user"].get("language") == language))


@pytest.fixture
def setup(db_session, monkeypatch):
    from app.core.ai_config import ai_config
    from app.services import story_plan_service

    case = Case(canonical_title="Flow Test Case", slug=f"flow-{uuid.uuid4().hex[:8]}", language="en", status="researched")
    db_session.add(case)
    db_session.commit()
    for i in range(3):
        db_session.add(Fact(case_id=case.id, claim=f"Fact number {i} happened in 1892.",
                            confidence=0.9, evidence_strength="strong", category="event"))
    plan = {"title": "T", "central_question": "Q", "opening_strategy": "cold",
            "acts": [{"id": a, "title": a, "purpose": "p", "key_beats": ["b"], "target_words": 100,
                      "evidence_ids": ["F001"], "do_not_reveal": []} for a in ACTS]}
    row = StoryPlan(case_id=case.id, version=1, language="en", target_minutes=90.0, status="approved",
                    plan_json=json.dumps(plan), validation_json="{}", evidence_fingerprint="x")
    db_session.add(row)
    db_session.commit()

    async def ensure(self, case, minutes, language, **kw):
        return row

    monkeypatch.setattr(story_plan_service.StoryPlanService, "ensure_approved", ensure)
    monkeypatch.setattr(ai_config.language_pipeline, "words_per_minute", {"en": 10, "de": 10, "fa": 10, "ar": 10})
    monkeypatch.setattr(ai_config.language_pipeline, "long_draft_minutes", 90)
    monkeypatch.setattr(ai_config.language_pipeline.gates, "max_unsupported_figures", 99)
    # the draft's editorial review is one more inspector call; its own tests turn it on (test_draft_review)
    monkeypatch.setattr(ai_config.language_pipeline.draft_review, "enabled", False)
    # The Persian and Arabic writers have roles of their own (their model is chosen apart). These tests
    # follow the pipeline's flow, not its routing, so they see one writer role per stage for every
    # language; test_language_writer_models checks the routing itself.
    for language in ("fa", "ar"):
        monkeypatch.setattr(ai_config.agents[f"language.rewrite.{language}"], "role", "language_rewrite_writer")
        monkeypatch.setattr(ai_config.agents[f"language.final.{language}"], "role", "language_final_writer")
    monkeypatch.setattr(ai_config.agents["language.length_correction.fa_ar"], "role", "language_final_writer")
    return case


def _pipeline(gen):
    return LanguagePipeline(gen=gen, embedder=None)


def test_the_draft_is_written_once_for_all_languages_and_stages_get_their_inputs(setup):
    gen = FakeGen()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["fa", "en", "de", "ar"], 60))
    assert gen.count("language_draft_writer") == 2          # one call per act, one draft
    assert [lg["status"] for lg in out["languages"]] == ["done"] * 4
    # every language reads the same English master and the plan, never the evidence, with its own prompt
    rewrites = {c["user"]["language"]: c for c in gen.calls if c["role"] == "language_rewrite_writer"}
    assert set(rewrites) == {"fa", "en", "de", "ar"} and len({c["system"] for c in rewrites.values()}) == 4
    assert len({c["cache_key"] for c in gen.calls if c["cache_key"]}) == 1
    for language, call in rewrites.items():
        request = (call["prefix"] or "") + json.dumps(call["user"])
        assert '"english_master"' in request and "supporting_text" not in request and '"evidence"' not in request, language
    # stage 3 sees only the stage-2 text, the plan and the counts
    for call in (c for c in gen.calls if c["role"] == "language_inspector"):
        request = (call["prefix"] or "") + json.dumps(call["user"])
        assert "english_master" not in request and "supporting_text" not in request and "evidence" not in request
    # stage 4 gets the stage-2 text and the stage-3 critique
    for call in (c for c in gen.calls if c["role"] == "language_final_writer"):
        assert call["user"]["critique"]["items"][0]["severity"] == "high"
        assert call["user"]["last_version"].startswith("[[ACT:act1]]")


def test_every_stage_is_its_own_row_and_each_language_records_the_text_it_derives_from(setup, db_session):
    out = asyncio.run(_pipeline(FakeGen()).run_case(setup.id, ["en", "fa"], 60))
    db_session.expire_all()
    draft = db_session.get(StoryVersion, out["draft_version_id"])
    assert draft.kind == DRAFT and draft.language == "en" and draft.minutes is not None
    assert draft.approved_by == "automatic" and draft.approved_at is not None
    english_rows = db_session.query(StoryVersion).filter(
        StoryVersion.draft_version_id == draft.id, StoryVersion.kind.in_((REWRITE, INSPECTION, FINAL, REVIEW))).all()
    assert sorted((r.language, r.kind) for r in english_rows) == sorted(("en", k) for k in (REWRITE, INSPECTION, FINAL))
    english = next(r for r in english_rows if r.kind == FINAL)
    pipeline = json.loads(english.narrative_structure)["pipeline"]
    assert pipeline["derived_from"] == {"id": draft.id, "kind": "draft", "language": "en",
                                        "prompt_sha256": draft.writer_prompt_sha256, "text_hash": draft.text_hash}
    assert english.approved_by == "automatic" and english.reasoning_effort == "high" and english.cost_eur > 0
    # Persian is re-told from the approved English final, not from the draft
    persian_rows = db_session.query(StoryVersion).filter(
        StoryVersion.draft_version_id == english.id,
        StoryVersion.kind.in_((REWRITE, INSPECTION, FINAL, FIDELITY))).all()
    assert sorted((r.language, r.kind) for r in persian_rows) == sorted(
        ("fa", k) for k in (REWRITE, INSPECTION, FINAL, FIDELITY))
    # a review is made on one text version and points at it, not at the text that version was re-told from
    reviews = db_session.query(StoryVersion).filter_by(case_id=setup.id, kind=REVIEW).all()
    assert sorted((r.language, r.draft_version_id) for r in reviews) == sorted([
        ("en", english.id), ("fa", next(r.id for r in persian_rows if r.kind == FINAL))])
    persian = next(r for r in persian_rows if r.kind == FINAL)
    assert json.loads(persian.narrative_structure)["pipeline"]["derived_from"] == {
        "id": english.id, "kind": "final", "language": "en", "prompt_sha256": english.writer_prompt_sha256,
        "text_hash": english.text_hash}
    assert persian.approved_by == "automatic" and persian.writer_prompt_sha256


def test_stage_two_keeps_to_the_60_to_70_minute_range_and_a_custom_request_shifts_it(setup):
    gen = FakeGen()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    task = next(c for c in gen.calls if c["role"] == "language_rewrite_writer")["user"]
    assert (task["target_minutes_min"], task["target_minutes_max"]) == (60, 70)
    gen2 = FakeGen()
    asyncio.run(_pipeline(gen2).run_case(setup.id, ["en"], 75))
    task = next(c for c in gen2.calls if c["role"] == "language_rewrite_writer")["user"]
    assert (task["target_minutes_min"], task["target_minutes_max"]) == (75, 85)
    assert final_range("en", 75).words_max == 850


def test_a_rerun_resumes_without_calling_a_stage_that_has_its_row(setup):
    first = FakeGen()
    asyncio.run(_pipeline(first).run_case(setup.id, ["en"], 60))
    again = FakeGen()
    out = asyncio.run(_pipeline(again).run_case(setup.id, ["en"], 60))
    assert again.count("language_draft_writer") == 0
    assert [c["role"] for c in again.calls if c["role"].startswith("language_")] == []
    assert out["draft_reused"] and out["languages"][0]["reused"] == [REWRITE, INSPECTION, FINAL]


def test_one_failing_language_leaves_the_others_and_the_draft(setup, db_session):
    gen = FakeGen(fail_language="ar")
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["fa", "ar", "de"], 60))
    status = {lg["language"]: lg["status"] for lg in out["languages"]}
    assert status == {"fa": "done", "ar": "failed", "de": "done"}
    assert gen.count("language_draft_writer") == 2
    # resuming the failed language alone does not write the draft again
    gen.fail_language = None
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["ar"], 60))
    assert out["languages"][0]["status"] == "done" and gen.count("language_draft_writer") == 2


def test_the_draft_is_always_ninety_minutes_in_english_whatever_the_languages(setup):
    from app.core.ai_config import ai_config

    gen = FakeGen()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["fa"], 60))
    task = next(c for c in gen.calls if c["role"] == "language_draft_writer")["user"]
    assert task["language"] == ai_config.language_pipeline.draft_language
    assert task["target_words"] == 90 * 10          # 90 minutes at the (patched) 10 words a minute
    assert ai_config.language_pipeline.draft_language == "en"


def test_the_owners_instruction_runs_stage_four_again_through_the_gates(setup):
    from app.language_pipeline.orchestrator import LanguagePipeline

    gen = FakeGen()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    first = out["languages"][0]["final_version_id"]
    row = asyncio.run(LanguagePipeline(gen=gen).improve_final(setup.id, "en", "Make act 2 shorter.", 60))
    assert row.id != first and row.kind == "final" and row.status == "ready"
    repeat = [c for c in gen.calls if c["role"] == "language_final_writer"][-1]["user"]
    assert repeat["critique"]["items"][0]["instruction"] == "Make act 2 shorter."
    assert "gate_failures" not in repeat
    assert gen.count("language_rewrite_writer") == 1 and gen.count("language_draft_writer") == 2


def test_the_draft_is_written_act_by_act_after_the_story_so_far(setup, db_session):
    gen = FakeGen()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    drafts = [c for c in gen.calls if c["role"] == "language_draft_writer"]
    assert [c["user"]["act"]["id"] for c in drafts] == ["act1", "act2"]
    assert drafts[0]["user"]["story_so_far"] == "" and drafts[1]["user"]["story_so_far"].startswith("dword")
    assert [a["id"] for a in drafts[0]["user"]["acts_after"]] == ["act2"] and drafts[1]["user"]["acts_after"] == []
    assert len({c["prefix"] for c in drafts}) == 1          # one cached prefix for every act
    row = db_session.get(StoryVersion, out["draft_version_id"])
    assert row.status == "ready" and [s["id"] for s in json.loads(row.narrative_structure)["sections"]] == ["act1", "act2"]


def test_a_draft_that_stopped_half_way_goes_on_from_the_acts_it_has(setup, monkeypatch):
    from app.language_pipeline.orchestrator import LanguagePipeline

    gen = FakeGen()
    calls = {"n": 0}
    real = LanguagePipeline._store_draft

    def stop_after_the_first_act(*a, **k):
        row = real(*a, **k)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the process died")
        return row

    monkeypatch.setattr(LanguagePipeline, "_store_draft", staticmethod(stop_after_the_first_act))
    import pytest
    with pytest.raises(RuntimeError):
        asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    again = FakeGen()
    out = asyncio.run(_pipeline(again).run_case(setup.id, ["en"], 60))
    assert [c["user"]["act"]["id"] for c in again.calls if c["role"] == "language_draft_writer"] == ["act2"]
    assert out["languages"][0]["status"] == "done"








def test_an_update_video_carries_the_earlier_coverage_into_the_draft_and_its_opening_is_gated_when_configured(
        setup, db_session, monkeypatch):
    # the follow-up opening gate is not one of English's blocking gates by default; this run turns it on
    from app.core.ai_config import ai_config

    stage = ai_config.language_pipeline.language_stages["en"]
    monkeypatch.setattr(stage, "blocking_gates", ["markers", "length", "follow_up"])
    follow_up = {"follow_up_id": 7, "original_title": "The Lena Sommer Case", "episode_number": 12,
                 "original_published": "2025-03-01", "development": "A neighbour was convicted.",
                 "intro": "We first told this story in Episode 12."}
    gen = FakeGen()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60, follow_up=follow_up))
    # the draft is written to the earlier coverage; the film stages re-tell that draft
    call = next(c for c in gen.calls if c["role"] == "language_draft_writer")
    assert "Lena Sommer" in (call["prefix"] or "") + json.dumps(call["user"])
    # the fake English text does not open with the earlier video: the language fails, with no second call
    assert gen.count("language_final_writer") == 1
    assert out["languages"][0]["status"] == "failed" and "follow_up" in out["languages"][0]["error"]
    final = db_session.query(StoryVersion).filter_by(case_id=setup.id, kind="final", language="en").one()
    assert final.status == "needs_revision" and final.approved_at is None
    assert json.loads(final.critic_notes)["quality_gates"]["failures"] == ["follow_up"]


def test_an_act_is_written_one_scene_per_key_beat_each_after_the_story_so_far(setup, db_session):
    plan = db_session.query(StoryPlan).filter_by(case_id=setup.id).one()
    data = json.loads(plan.plan_json)
    data["acts"][0]["key_beats"] = ["first beat", "second beat", "third beat"]
    plan.plan_json = json.dumps(data)
    db_session.commit()
    gen = FakeGen()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    scenes = [c["user"] for c in gen.calls if c["role"] == "language_draft_writer"]
    assert [(s["act"]["id"], s["beat_index"], s["beats_in_act"]) for s in scenes] == [
        ("act1", 1, 3), ("act1", 2, 3), ("act1", 3, 3), ("act2", 1, 1)]
    assert [s["beat"] for s in scenes[:3]] == ["first beat", "second beat", "third beat"]
    assert scenes[0]["beats_after_in_this_act"] == ["second beat", "third beat"]
    assert [s["unit_target_words"] for s in scenes] == [150, 150, 150, 450]     # the act's 450 words, split
    assert scenes[0]["story_so_far"] == "" and len(scenes[3]["story_so_far"].split()) == 450
    row = db_session.get(StoryVersion, out["draft_version_id"])
    sections = {s["id"]: s["text"] for s in json.loads(row.narrative_structure)["sections"]}
    assert len(sections["act1"].split("\n\n")) == 3 and row.status == "ready"


class OrderedGen(FakeGen):
    """Every call takes a moment, so languages that run together overlap."""

    def __init__(self):
        super().__init__()
        self.events = []

    async def generate_text(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        language = json.loads(user).get("language")
        self.events.append(("start", role, language))
        await asyncio.sleep(0.01)
        result = await super().generate_text(role, system, user, images, prefix=prefix, cache_key=cache_key)
        self.events.append(("end", role, language))
        return result


def _first(events, kind, language):
    return events.index((kind, "language_rewrite_writer", language))


def _with_english_done(setup):
    """English is the base of every other language: it runs first, once."""
    out = asyncio.run(_pipeline(FakeGen()).run_case(setup.id, ["en"], 60))
    assert out["languages"][0]["status"] == "done"


def test_with_the_cache_primer_the_first_language_finishes_stage_two_before_the_others_start(setup):
    _with_english_done(setup)
    gen = OrderedGen()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["fa", "de", "ar"], 60))
    assert [lg["status"] for lg in out["languages"]] == ["done"] * 3
    primed = _first(gen.events, "end", "fa")
    assert all(primed < _first(gen.events, "start", lg) for lg in ("de", "ar"))
    # the others then run together, not one after the other
    assert max(_first(gen.events, "start", lg) for lg in ("de", "ar")) < min(
        _first(gen.events, "end", lg) for lg in ("de", "ar"))


def test_without_the_cache_primer_all_languages_start_stage_two_together(setup, monkeypatch):
    from app.core.ai_config import ai_config

    _with_english_done(setup)
    monkeypatch.setattr(ai_config.language_pipeline, "stage2_cache_primer", False)
    gen = OrderedGen()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["fa", "de", "ar"], 60))
    assert max(_first(gen.events, "start", lg) for lg in ("fa", "de", "ar")) < min(
        _first(gen.events, "end", lg) for lg in ("fa", "de", "ar"))


def test_a_primer_language_that_fails_does_not_hold_the_others_back(setup):
    _with_english_done(setup)
    gen = OrderedGen()
    gen.fail_language = "fa"
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["fa", "de", "ar"], 60))
    assert [lg["status"] for lg in out["languages"]] == ["failed", "done", "done"]


def test_a_short_scene_is_accepted_as_it_comes_with_one_call_and_never_written_again(setup, db_session):
    plan = db_session.query(StoryPlan).filter_by(case_id=setup.id).one()
    data = json.loads(plan.plan_json)
    data["acts"][0]["key_beats"] = ["first beat", "second beat", "third beat"]
    plan.plan_json = json.dumps(data)
    db_session.commit()
    gen = FakeGen()
    gen.short_drafts = 1                                    # the first scene comes back with 20 words
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    drafts = [c["user"] for c in gen.calls if c["role"] == "language_draft_writer"]
    assert [d["act"]["id"] for d in drafts] == ["act1", "act1", "act1", "act2"]      # one call per scene
    assert all("previous_attempt" not in d for d in drafts)
    assert out["languages"][0]["status"] == "done"
    draft = db_session.get(StoryVersion, out["draft_version_id"])
    assert len(json.loads(draft.narrative_structure)["pipeline"]["scenes"][0]["text"].split()) == 20


def test_a_draft_that_is_too_short_as_a_whole_fails_with_its_reason_and_pays_nothing_twice(setup, db_session):
    import pytest

    from app.language_pipeline.stages import StageFailed
    from app.language_pipeline.store import StageStore

    gen = FakeGen()
    gen.short_drafts = 99
    with pytest.raises(StageFailed, match="at least"):
        asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    assert gen.count("language_draft_writer") == 2 and gen.count("language_rewrite_writer") == 0
    assert gen.count("english_evidence_auditor") == 0                       # nothing after a failed draft
    again = FakeGen()
    with pytest.raises(StageFailed, match="at least"):                      # the same verdict, no new call
        asyncio.run(_pipeline(again).run_case(setup.id, ["en"], 60))
    assert again.calls == []
    # the owner can set the failed draft aside; a new one is then written, one call per scene
    failed = db_session.query(StoryVersion).filter_by(case_id=setup.id, kind="draft").one()
    StageStore(db_session).retire(failed, "too short for the film versions")
    third = FakeGen()
    out = asyncio.run(_pipeline(third).run_case(setup.id, ["en"], 60))
    assert third.count("language_draft_writer") == 2 and out["languages"][0]["status"] == "done"

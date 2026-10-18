"""The deterministic parts: figures in any script, the gates, the coverage."""

import numpy as np

from app.language_pipeline import figures
from app.language_pipeline.coverage import EvidenceCoverage, Telling
from app.language_pipeline.gates import FinalGates, GateResult
from app.language_pipeline.lengths import final_range
from app.language_pipeline.materials import SharedMaterial


def _material(facts, acts=None, extra_plan=None):
    pack = {"facts": facts, "timeline": [], "contradictions": [], "approved_context": []}
    plan = {"acts": acts or [{"id": "act1", "evidence_ids": [f["id"] for f in facts], "do_not_reveal": []}],
            **(extra_plan or {})}
    return SharedMaterial(case_id=1, plan_id=1, plan=plan, pack=pack, draft_pack=pack, fingerprint="f")


def _fact(i, claim, **kw):
    return {"id": f"F{i:03d}", "claim": claim, "people": kw.get("people", []),
            "locations": kw.get("locations", [])}


def test_figures_are_read_the_same_in_every_script_and_separator():
    text = "1892, ۱۸۹۲, ١٨٩٢, 300,000 and 300.000 and ٣٠٠٬٠٠٠"
    assert set(figures.figures_in(text)) == {"1892", "300000"}


def test_number_words_in_the_evidence_support_digits_in_the_text():
    allowed = figures.supported_figures({"claim": "The gap was ninety minutes; forty-one blows."})
    assert {"90", "41"} <= allowed
    assert figures.unsupported("۹۰ دقیقه و 77 ضربه", allowed) == [
        {"figure": "77", "context": "90 دقیقه و 77 ضربه"}]


def test_reveal_order_finds_withheld_evidence_told_in_its_act():
    facts = [_fact(1, "the murderer is named"), _fact(2, "a harmless detail")]
    acts = [{"id": "act1", "do_not_reveal": ["F001"], "evidence_ids": ["F002"]},
            {"id": "act2", "do_not_reveal": [], "evidence_ids": ["F001"]}]
    material = _material(facts, acts)
    tellings = {"F001": Telling("F001", "act1", 0.9, "She named the murderer."),
                "F002": Telling("F002", "act1", 0.9, "detail")}
    early = EvidenceCoverage.told_before_their_act(material, tellings)
    assert [(e["act"], e["evidence_id"]) for e in early] == [("act1", "F001")]
    tellings["F001"] = Telling("F001", "act2", 0.9, "later")
    assert EvidenceCoverage.told_before_their_act(material, tellings) == []


class _Embedder:
    """Sentences containing a claim's key word share its direction."""

    keys = ["murderer", "detail"]

    async def embed(self, texts):
        rows = []
        for t in texts:
            v = np.array([1.0 if k in t.lower() else 0.0 for k in self.keys], dtype=np.float32)
            rows.append(v + 0.01)
        return np.array(rows)


def test_coverage_places_each_evidence_item_in_the_act_that_tells_it():
    import asyncio

    material = _material([_fact(1, "the murderer is named"), _fact(2, "a detail")])
    sections = [{"id": "act1", "text": "Only a detail was mentioned here today."},
                {"id": "act2", "text": "Then the murderer was named in court."}]
    tellings = asyncio.run(EvidenceCoverage(_Embedder()).tellings(material, sections))
    assert {k: v.act_id for k, v in tellings.items()} == {"F001": "act2", "F002": "act1"}


def test_gate_failures_are_listed_for_the_final_writer():
    material = _material([_fact(1, "Fact in 1892.")])
    result = GateResult()
    FinalGates._length(result, "one two three", final_range("de", 60))
    FinalGates._figures(result, material, "Born 1999 and 2001 and 2003 and 2005.")
    assert [f["gate"] for f in result.failures] == ["length", "figures"]
    assert not result.passed and result.as_dict()["failures"] == ["length", "figures"]


def test_facts_beyond_the_pack_go_to_the_act_whose_evidence_they_are_closest_to():
    import asyncio

    from app.language_pipeline.assign import extra_evidence_by_act

    pack = {"facts": [_fact(1, "arrest of the suspect"), _fact(2, "the funeral was held")],
            "timeline": [], "contradictions": [], "approved_context": []}
    draft_pack = {**pack, "facts": [*pack["facts"], _fact(3, "the suspect was arrested again"),
                                    _fact(4, "mourners at the funeral")]}
    plan = {"acts": [{"id": "act1", "evidence_ids": ["F001"], "purpose": "the arrest"},
                     {"id": "act2", "evidence_ids": ["F002"], "purpose": "the funeral"}]}
    material = SharedMaterial(case_id=1, plan_id=1, plan=plan, pack=pack, draft_pack=draft_pack,
                              fingerprint="f")

    class Embedder:
        async def embed(self, texts):
            keys = ["arrest", "funeral"]
            return np.array([[1.0 if k in t.lower() else 0.0 for k in keys] for t in texts],
                            dtype=np.float32) + 0.01

    assert asyncio.run(extra_evidence_by_act(material, Embedder())) == {
        "act1": ["F003"], "act2": ["F004"]}
    assert asyncio.run(extra_evidence_by_act(material, None)) == {"act1": [], "act2": []}


def test_the_estimate_counts_scenes_stages_and_gates_and_is_labelled_an_estimate():
    from app.language_pipeline.estimate import NOTE, estimate

    facts = [_fact(i, "x" * 400, people=["Abby Borden"] if i == 1 else []) for i in range(1, 51)]
    acts = [{"id": "act1", "key_beats": ["a", "b", "c"], "target_words": 100, "evidence_ids": []},
            {"id": "act2", "key_beats": ["d", "e"], "target_words": 100, "evidence_ids": []}]
    material = _material(facts, acts)
    result = estimate(material, ["ar", "en", "fa", "de"], 60)
    assert result["estimate"] is True and result["note"] == NOTE
    by = {(lg["stage"], lg["language"]): lg for lg in result["lines"]}
    from app.core.ai_config import ai_config

    factor = ai_config.language_pipeline.estimate.draft_attempt_factor
    assert by[("draft", None)]["calls"] == round(5 * factor)       # one call per key beat, and the repeats
    assert {s for s, _ in by} == {"draft", "audit", "draft_review", "rewrite", "inspect", "final", "review",
                                  "fidelity", "audit_final", "audit_fix"}               # no fact-check loop
    assert by[("draft_review", None)]["calls"] == 1                # the inspector reads the draft once
    # a language is written twice and inspected once (one final call), then its delivered text is reviewed
    # and checked: English against the evidence, every other language for its meaning against the English
    assert {by[("final", lg)]["calls"] for lg in ("en", "fa", "de", "ar")} == {1}
    assert {lg for s, lg in by if s == "fidelity"} == {"fa", "de", "ar"} and ("audit_fix", None) in by
    audit_calls = by[("audit", None)]["calls"] + by[("audit_final", None)]["calls"]
    assert result["calls"] == round(5 * factor) + audit_calls + 1 + 1 + 4 * 4 + 3    # draft review, audit fix, 4 chains of 4, 3 fidelity checks
    assert "revision round" in result["contingencies"]
    assert result["large_calls"] == result["calls"]
    assert result["total_eur"] == round(sum(lg["eur"] for lg in result["lines"]), 2)
    assert all(lg["model"] for lg in result["lines"])


def _ledger(db_session, case_id, rows):
    from app.db.models import ProviderCall

    for stage, calls, tin, reasoning, tout, eur in rows:
        for _ in range(calls):
            db_session.add(ProviderCall(case_id=case_id, host="budget-ledger", stage=stage, model="gpt-6.1-sol",
                                        tokens_in=tin, tokens_cached=0, tokens_reasoning=reasoning,
                                        tokens_out=tout, cost_eur=eur, method="POST", path="/x"))
    db_session.commit()


def test_the_estimate_is_shown_beside_the_ledger_per_stage_and_language(db_session):
    import uuid

    from app.db.models import Case
    from app.language_pipeline import calibration
    from app.language_pipeline.estimate import estimate

    case = Case(canonical_title="Cal Case", slug=f"c-{uuid.uuid4().hex[:8]}", language="en", status="researched")
    db_session.add(case)
    db_session.commit()
    _ledger(db_session, case.id, [("language_draft", 4, 1000, 0, 500, 0.10),
                                  ("language_final:fa", 3, 2000, 100, 1100, 0.20),
                                  ("language_gates:fa", 2, 500, 50, 80, 0.05)])
    facts = [_fact(i, "x" * 400) for i in range(1, 11)]
    acts = [{"id": "act1", "key_beats": ["a", "b"], "target_words": 100, "evidence_ids": []}]
    result = estimate(_material(facts, acts), ["fa"], 60)
    rows = {(r["stage"], r["language"]): r for r in calibration.compare(db_session, case.id, result)}
    assert rows[("draft", None)]["actual_calls"] == 4 and rows[("draft", None)]["actual_eur"] == 0.4
    assert rows[("final", "fa")]["actual_calls"] == 3 and rows[("final", "fa")]["estimated_eur"] > 0
    assert rows[("final", "fa")]["ratio"] == round(0.6 / rows[("final", "fa")]["estimated_eur"], 2)
    assert rows[("rewrite", "fa")]["actual_eur"] is None         # nothing run yet: no actual number
    material = _material(facts, acts)
    factors = calibration.measure(db_session, case.id, material, ["fa"])
    assert factors["draft_attempt_factor"] == 2.0 and factors["ledger_eur"] == 1.1


def test_the_estimate_leaves_out_the_stages_that_already_have_their_row():
    from app.language_pipeline.estimate import estimate

    facts = [_fact(i, "x" * 400) for i in range(1, 11)]
    acts = [{"id": "act1", "key_beats": ["a", "b"], "target_words": 100, "evidence_ids": []}]
    material = _material(facts, acts)
    stages = lambda existing: {line["stage"] for line in estimate(material, ["en"], 60, existing)["lines"]}  # noqa: E731
    chain = {"rewrite", "inspect", "final", "review", "audit_final", "audit_fix"}      # English: delivered text checked
    assert stages(frozenset()) == {"draft", "audit", "draft_review"} | chain
    assert stages(frozenset({"draft"})) == {"audit", "draft_review"} | chain
    assert stages(frozenset({"draft", "audit"})) == {"draft_review"} | chain
    assert stages(frozenset({"draft", "audit", "draft_review"})) == chain


def test_the_draft_is_priced_at_the_measured_cache_share_with_the_worst_run_as_the_comparison():
    from app.language_pipeline.estimate import estimate

    facts = [_fact(i, "x" * 400) for i in range(1, 11)]
    acts = [{"id": "act1", "key_beats": ["a", "b", "c"], "target_words": 100, "evidence_ids": []}]
    material = _material(facts, acts)
    none = estimate(material, ["en"], 60)
    assert none["cache"]["source"].startswith("none measured") and none["cache"]["share_used"] == 0.0
    good = estimate(material, ["en"], 60, cache={"overall": 0.9, "worst_run": 0.2, "runs": 5})
    assert good["cache"] == {"source": "ledger", "runs_measured": 5, "share_used": 0.9, "worst_run_share": 0.2}
    # Gemini Flash has no cached-input price, so the draft costs the same at any cache share
    assert good["scenarios"]["measured_cache"] == good["scenarios"]["worst_measured_run"] == none["total_eur"]


def test_with_a_model_that_has_a_cached_input_price_the_measured_share_lowers_the_draft(monkeypatch):
    from app.core.ai_config import ai_config
    from app.language_pipeline.estimate import estimate

    monkeypatch.setitem(ai_config.generation_provider().routing, "language_draft_writer", "premium")   # Sonnet
    facts = [_fact(i, "x" * 400) for i in range(1, 11)]
    acts = [{"id": "act1", "key_beats": ["a", "b", "c"], "target_words": 100, "evidence_ids": []}]
    material = _material(facts, acts)
    none = estimate(material, ["en"], 60)
    good = estimate(material, ["en"], 60, cache={"overall": 0.9, "worst_run": 0.2, "runs": 5})
    assert good["scenarios"]["measured_cache"] < good["scenarios"]["worst_measured_run"] < none["total_eur"]
    assert good["total_eur"] == good["scenarios"]["measured_cache"]


def test_the_cache_share_is_measured_per_run_on_the_ledger_and_the_worst_run_is_the_conservative_one(db_session):
    import uuid
    from datetime import timedelta

    from app.db.models import Case, ProviderCall
    from app.language_pipeline.calibration import measure_cache_share
    from app.utils import utc_now

    case = Case(canonical_title="Cache", slug=f"c-{uuid.uuid4().hex[:8]}", language="en", status="researched")
    db_session.add(case)
    db_session.commit()
    start = utc_now() - timedelta(days=1)
    plan = [(0, 1000, 900), (10, 1000, 900), (20, 1000, 0),            # run 1: 60 percent
            (4000, 1000, 0), (4010, 1000, 0)]                          # run 2, hours later: nothing cached
    for seconds, tin, cached in plan:
        db_session.add(ProviderCall(case_id=case.id, host="budget-ledger", stage="language_draft", model="m",
                                    method="CALL", path="/x", tokens_in=tin, tokens_cached=cached,
                                    created_at=start + timedelta(seconds=seconds)))
    db_session.commit()
    own = measure_cache_share(db_session)
    assert own["runs"] >= 2 and own["worst_run"] == 0.0 and own["best_run"] >= 0.6

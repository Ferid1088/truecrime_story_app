"""Re-run only the visual stages of a finished film on the real app and database.

    python scripts/visual_stages.py CASE_ID --blueprint ID --language en            # print the estimate
    python scripts/visual_stages.py CASE_ID --blueprint ID --language en --go       # run it

Stages: the stored verdicts judged again under the current rules (free), the vision check and the
second pass of what is unchecked or open, the picture supply for the film's own length and for the
length asked with --target-minutes (search rounds under the research cost guard), a new visual plan,
the production search for weak sentences, a new production script from the stored narration (no new
script, no new audio), the audit of its pictures and the coverage repair. Nothing is rendered. The
case budget is checked before every paid step; the estimate is printed first, the actual spend after."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import (  # noqa: E402
    Case,
    EditorialBlueprint,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary import jobs as J  # noqa: E402
from app.documentary.visuals import funnel, redecide, storage_budget, supply  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402
from app.services.cost_model import load_pricing  # noqa: E402


def spent(db, case_id: int) -> float:
    return BudgetService(db).snapshot(case_id).spent_eur


def estimate(db, case: Case, film_seconds: float, target_seconds: float) -> dict:
    """Counts and EUR of the paid steps, from the measured assumptions in pricing.json."""
    a = load_pricing()["assumptions"]["visual_research"]
    rate = float(load_pricing()["exchange_rates"]["usd_to_eur"])
    models = load_pricing()["models"]
    vision = (a["vision_input_tokens"] * models["premium"]["input_usd_per_1m_tokens"]
              + a["vision_output_tokens"] * models["premium"]["output_usd_per_1m_tokens"]) / 1e6 * rate
    second = (a["second_input_tokens"] * models["sol"]["input_usd_per_1m_tokens"]
              + a["second_output_tokens"] * models["sol"]["output_usd_per_1m_tokens"]) / 1e6 * rate
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == case.id, VisualAsset.provider != "generated")
    unchecked = sum(1 for x in assets if x.verification_status == "unverified" and x.local_path
                    and x.asset_type == "photo")
    judged = redecide.run(db, case.id, apply=False)
    open_nonperson = sum(1 for x in assets if x.verification_status == "needs_review"
                         and (x.subject_type or "") in ai_config.visual_verification.verified_min_confidence_by_subject)
    price = float(load_pricing()["search"]["brave_web"]["eur_per_request"])
    rounds = ai_config.supply.max_searches - 1
    requests = rounds * int(a["queries_per_round"])
    new_checks = int(requests * 3)          # about three new candidates checked per query on average (measured: 701/56 stored 150)
    rows = {"unchecked_photos_to_check": unchecked,
            "pictures_without_a_verdict_judged_again_free": len(judged["changed"]),
            "needs_review_for_second_pass": open_nonperson}
    eur = {"vision_checks": round((unchecked + new_checks) * vision, 2),
           "second_pass": round((open_nonperson + int(new_checks * a["second_pass_share"])) * second, 2),
           "searches": round(requests * price, 2)}
    eur["director_and_audit_allowance"] = 3.0
    return {**rows, "supply_rounds_at_most": rounds, "search_requests_at_most": requests,
            "new_candidates_checked_at_most": new_checks, "eur": eur, "eur_total": round(sum(eur.values()), 2),
            "research_cost_guard_eur": ai_config.supply.research_cost_cap_eur,
            "film_seconds": film_seconds, "target_seconds": target_seconds}


async def plan_from_scratch(db, case, bp_row, blueprint, old_plan, requirements, ap_row, film_seconds,
                            target_minutes, before, report) -> VisualPlan:
    """The research stages and a new visual plan (the part --reuse-plan skips)."""
    from app.documentary.visuals.director import VisualDirector
    from app.documentary.visuals.gaps import fill_visual_gaps
    from app.documentary.visuals.generated import materialize

    report["redecide"] = {k: v for k, v in redecide.run(db, case.id, apply=True).items() if k != "changed"}

    async def research(queries):
        from app.agents.visual_research import VisualResearchAgent

        return await VisualResearchAgent().run(db, case, queries)

    async def verify():
        return await J.verify_candidates(db, case, blueprint, requirements, None, "preview")

    report["check"] = await verify()
    report["supply_film"] = await supply.top_up(db, case, requirements, blueprint, None, film_seconds,
                                                research, verify, "preview")
    report["supply_target"] = await supply.top_up(db, case, requirements, blueprint, None,
                                                  target_minutes * 60.0, research, verify, "preview")
    report["spent_after_research_eur"] = round(spent(db, case.id) - before, 4)
    new_plan = VisualPlan(case_id=case.id, blueprint_id=bp_row.id, version=old_plan.version + 1,
                          status="requirements", requirements_json=old_plan.requirements_json)
    db.add(new_plan)
    db.commit()
    await VisualDirector().create(db, case, new_plan, bp_row, ap_row, profile="preview")
    await materialize(db, case, new_plan)
    report["gaps"] = await fill_visual_gaps(db, case, new_plan, bp_row, ap_row, profile="preview")
    return new_plan


async def run(case_id: int, blueprint_id: int, language: str, target_minutes: float, go: bool,
              reuse_plan: int | None = None) -> dict:
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        bp_row = db.get(EditorialBlueprint, blueprint_id)
        blueprint = json.loads(bp_row.blueprint_json or "{}")
        master = db.get(StoryVersion, bp_row.story_version_id)
        version = J.latest_spoken(db, master, language, bp_row.id)
        manifest = J._load_manifest(case.id, version)
        film_seconds = float(manifest["timeline"].get("duration") or manifest.get("duration_seconds") or 0.0)
        est = estimate(db, case, film_seconds, target_minutes * 60.0)
        print("ESTIMATE", json.dumps(est, indent=1))
        if not go:
            return {"estimate": est}
        before = spent(db, case.id)
        report: dict = {"estimate": est, "spent_before_eur": before}
        old_plan = J.latest_visual_plan(db, bp_row.id)
        requirements = json.loads(old_plan.requirements_json or "{}")
        ap_row = J.latest_audio_plan(db, bp_row.id)
        if reuse_plan is None:
            new_plan = await plan_from_scratch(db, case, bp_row, blueprint, old_plan, requirements, ap_row,
                                               film_seconds, target_minutes, before, report)
        else:
            new_plan = db.get(VisualPlan, reuse_plan)
        from app.documentary.production.script import build_production_script

        ps = await build_production_script(db, version, new_plan, manifest, mode="full")
        from app.documentary.visuals.auditor import audit_script, audit_summary
        from app.documentary.visuals.coverage_fill import ensure_coverage

        report["audit"] = audit_summary(await audit_script(db, ps))
        db.refresh(ps)
        report["coverage"] = await ensure_coverage(db, ps)
        db.refresh(ps)
        report.update(production_script_id=ps.id, visual_plan_id=new_plan.id,
                      film=funnel.film_stats(db, ps), storage=storage_budget.usage(db, case.id),
                      spent_total_eur=round(spent(db, case.id) - before, 4))
        return report
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", type=int)
    parser.add_argument("--blueprint", type=int, required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--target-minutes", type=float, default=60.0)
    parser.add_argument("--reuse-plan", type=int, help="skip research and direction: build from this plan")
    parser.add_argument("--go", action="store_true", help="run the paid steps (without it: the estimate only)")
    args = parser.parse_args()
    out = asyncio.run(run(args.case, args.blueprint, args.language, args.target_minutes, args.go, args.reuse_plan))
    print("REPORT", json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()

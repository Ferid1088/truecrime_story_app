"""The visual funnel report of a case, as markdown, plus its contact sheets.

    python scripts/visual_funnel.py CASE_ID [--plan PLAN_ID] [--out docs/visual_funnel.md]
                                    [--sheets DIR] [--beats B04,B05]

Read-only: it queries the database and replays the director's candidate ranking; it calls no
provider and writes nothing but the report and the sheets."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import ProductionScript, VisualPlan  # noqa: E402
from app.documentary.visuals import contact_sheet, funnel  # noqa: E402


def table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def case_section(db, case_id: int, plan_id: int | None, beats: list[str], sheets: Path | None) -> str:
    plans = db.query(VisualPlan).filter(VisualPlan.case_id == case_id).order_by(VisualPlan.id).all()
    plan = db.get(VisualPlan, plan_id) if plan_id else next(
        (p for p in reversed(plans) if p.status == "planned"), plans[-1])
    out = [f"## Case {case_id} (visual plan {plan.id}, blueprint {plan.blueprint_id})", ""]
    reasons = funnel.ai_config.visual_report_settings().rejection_reasons + [funnel.OTHER]
    out += ["### Stored pictures per stage and provider", "",
            table(["found during", "provider", "type", "stored", "with file", "verified",
                   "needs_review", "rejected", "unchecked"] + [f"rej: {r}" for r in reasons],
                  [[r["stage"], r["provider"], r["type"], r["stored"], r["downloaded"], r["verified"],
                    r["needs_review"], r["rejected"], r["unverified"]]
                   + [r["rejected_by"].get(x, 0) for x in reasons]
                   for r in funnel.provider_funnel(db, case_id)]), ""]
    out += ["### Research stages (before storage)", "",
            table(["job", "stage", "status", "detail"],
                  [[s["job"], s["stage"], s["status"], s["detail"]] for s in funnel.intake_stages(db, case_id)]), ""]
    out += ["### Beats: offered to the director and used", "",
            table(["beat", "seconds", "offered now", "stored with plan", "tiers", "pictures used"],
                  [[b["beat"], b["seconds"], b["offered_now"], b["stored_with_plan"], b["tiers"], b["used"]]
                   for b in funnel.beat_offers(db, plan)]), ""]
    for bid in beats:
        out += [f"### Why beat {bid} is short of candidates", "",
                table(["entity", "purpose", "kept away by (first rule)"],
                      [[k, v["purpose"], {x: n for x, n in v.items() if x not in ("purpose", "name")}]
                       for k, v in funnel.empty_beat_reasons(db, plan, bid).items()]), ""]
    rules = funnel.plan_rules(plan)
    out += ["### Director validator", "",
            f"shots in the plan: {funnel._loads(plan.validation_json, {}).get('plan', {}).get('shots')}; "
            f"adjustments: {dict(rules['adjustments'])}; warnings: {dict(rules['warnings'])}", ""]
    out += ["### Coverage and audit stages", "",
            table(["job", "stage", "detail"],
                  [[d.pop("job"), n, d] for n in ("visual_plan", "visual_audit", "visual_coverage")
                   for d in funnel.stage_details(db, case_id, n)]), ""]
    out += ["### Caps and rules", "",
            table(["cap", "limit", "reached", "hit"],
                  [[c["cap"], c["limit"], c["reached"], c["hit"]] for c in funnel.caps_hit(db, case_id, plan)]), ""]
    v = funnel.video_funnel(db, case_id)
    out += ["### Videos", "", f"found as sources: {v['found']}; by source class: {v['by_class']}; clips downloaded: "
            f"{v['downloaded']}; pieces: {v['pieces']}; usable (verified) cuts: {v['usable_cuts']}",
            f"clip assets: {dict(v['clip_assets'])}; clips by status: {dict(v['clips_by_status'])}", "",
            table(["use", "reason", "videos"], [[x["use"], x["reason"], x["videos"]] for x in v["by_use"]]), ""]
    scripts = db.query(ProductionScript).filter(ProductionScript.case_id == case_id).order_by(ProductionScript.id)
    out += ["### Fill ways and repairs per film", "",
            table(["script", "shots by way"],
                  [[x.id, dict(funnel.fill_ways_used(x))] for x in scripts]), ""]
    out += ["### Films", "",
            table(["script", "language", "status", "seconds", "shots", "real image shots", "distinct real",
                   "avg s per real shot", "video shots", "video s", "by kind"],
                  [[s.id, s.language, s.status, round(s.duration_seconds or 0), (f := funnel.film_stats(db, s))["shots"],
                    f["real_image_shots"], f["real_distinct"], f["real_avg_seconds"], f["video_shots"],
                    f["video_seconds"], {k: f"{x['shots']}/{x['seconds']}s" for k, x in f["by_kind"].items()}]
                   for s in scripts]), ""]
    if sheets is not None:
        made = contact_sheet.render_all(db, case_id, sheets / f"case_{case_id}")
        out += ["### Contact sheets", ""] + [f"- {verdict}: {', '.join(str(p) for p in paths)}"
                                             for verdict, paths in made.items()] + [""]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="+", type=int)
    parser.add_argument("--plan", type=int)
    parser.add_argument("--beats", default="")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--sheets", type=Path)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        text = "\n".join(case_section(db, c, args.plan, [b for b in args.beats.split(",") if b], args.sheets)
                         for c in args.cases)
    finally:
        db.close()
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()

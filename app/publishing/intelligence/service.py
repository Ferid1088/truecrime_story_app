"""The runs of the AI publishing plan: starting one (only on the owner's request), its progress, what it saved, the estimate shown before a click, and the application of its
result to the publications. The result is saved in PostgreSQL; reopening the Studio reads it back and never calls a model."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.base import SessionLocal
from app.db.models import AgentRun, Case, Publication, PublishingPlanRun
from app.documentary.pipeline.errors import JobPaused
from app.publishing.config import PublishingConfig
from app.publishing.intelligence import schedule as SCH
from app.publishing.intelligence.agents import PlanError, PublishingAI
from app.publishing.intelligence.inputs import Inputs, build, metadata_view, planner_view
from app.publishing.registry import PublicationRegistry
from app.services.budget import BudgetStopped
from app.services.cost_model import load_pricing
from app.utils import utc_now

CHARS_PER_TOKEN = 0.55           # Persian costs about 1.8 tokens per character on the writer's model (measured on the live runs: 20,000 characters of request -> 37,233 input tokens)
PLANNER_OUTPUT_TOKENS = 2500
CRITIC_OUTPUT_TOKENS_PER_ITEM = 120       # four scores, a risk word and a one-sentence direction
WRITER_OUTPUT_TOKENS_PER_ITEM = 1300      # measured: 4,384 to 7,597 output tokens for 5 or 6 destinations


class PlanRunError(RuntimeError):
    """A request the AI plan cannot take (no schedule for the language, a run already going, nothing to plan, up to date)."""


def effective_status(run: PublishingPlanRun, cfg: PublishingConfig) -> str:
    """`running` for longer than the configured time without a sign of life was cut off (a restart, a crash)."""
    limit = timedelta(seconds=cfg.intelligence.generation.running_timeout_seconds)
    return "interrupted" if run.status == "running" and utc_now() - _aware(run.updated_at) > limit else run.status


def _aware(moment):
    from datetime import UTC

    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def usage_of(db: Session, ids: list[int], case_id: int | None = None, since=None) -> dict:
    """What the run's model calls really used and cost: the tokens from the calls' own records, the cost from the provider ledger (the `budget-ledger` rows the calls wrote, failed calls
    included: a call that came back unusable was still billed)."""
    from app.core import observability
    from app.db.models import ProviderCall

    observability.flush()
    rows = db.query(AgentRun).filter(AgentRun.id.in_(ids or [-1])).order_by(AgentRun.id).all()
    ledger = []
    if rows and case_id is not None and since is not None:
        ledger = (db.query(ProviderCall).filter(ProviderCall.case_id == case_id, ProviderCall.host == "budget-ledger", ProviderCall.created_at >= since,
                                                ProviderCall.path.in_([f"/{r.agent_name}" for r in rows])).order_by(ProviderCall.id).all())
    eur = sum(r.cost_eur or 0.0 for r in ledger)
    rate = float(load_pricing()["exchange_rates"]["usd_to_eur"])
    detail = [{"label": r.agent_name, "model": r.model, "status": r.status, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
               "cost_eur": next((round(x.cost_eur or 0.0, 4) for x in ledger if x.path == f"/{r.agent_name}" and x.tokens_in == r.input_tokens), None)} for r in rows]
    return {"calls": len(rows), "failed_calls": sum(1 for r in rows if r.status == "failed"), "input_tokens": sum(r.input_tokens or 0 for r in rows), "output_tokens": sum(r.output_tokens or 0 for r in rows),
            "cost_eur": round(eur, 4), "cost_usd": round(eur / rate, 4) if rate else 0.0, "priced_calls": len(ledger), "cost_source": "provider ledger (computed from the reported tokens)",
            "models": sorted({r.model for r in rows if r.model}), "agent_run_ids": [r.id for r in rows], "detail": detail}


def estimate(registry: PublicationRegistry, inputs: Inputs) -> dict:
    """Planning figures for a run, from the size of what the models will read and the configured prices (the real cost is the ledger's, shown after the run)."""
    pricing = load_pricing()
    rate = float(pricing["exchange_rates"]["usd_to_eur"])

    def price(role: str, chars: int, out_tokens: int) -> float:
        spec = pricing["models"][ai_config.alias_for(role)]
        return (chars / CHARS_PER_TOKEN * spec["input_usd_per_1m_tokens"] + out_tokens * spec["output_usd_per_1m_tokens"]) / 1_000_000 * rate

    total = price("publishing_planner", len(json.dumps(planner_view(registry, inputs), ensure_ascii=False)) + len(prompt("publishing/planner")), PLANNER_OUTPUT_TOKENS)
    platforms = list(dict.fromkeys(d.split("/")[0] for d in inputs.destinations))
    for platform in platforms:
        view = metadata_view(registry, inputs, platform, None)
        total += price("publishing_metadata_writer", len(json.dumps(view, ensure_ascii=False)) + len(prompt("publishing/metadata")), len(view["items"]) * WRITER_OUTPUT_TOKENS_PER_ITEM)
    judged = [p for p in platforms if any(d == f"{p}/short" for d in inputs.destinations)] if registry.cfg.intelligence.generation.hook_min_score else []
    for platform in judged:
        view = metadata_view(registry, inputs, platform, None)
        total += price("publishing_hook_critic", len(json.dumps(view["items"], ensure_ascii=False)) + len(prompt("publishing/hook_critic")), len(view["items"]) * CRITIC_OUTPUT_TOKENS_PER_ITEM)
    return {"calls": 1 + len(platforms) + len(judged), "eur_low": round(total * 0.8, 3), "eur_high": round(total * 1.9, 3),
            "models": {role: pricing["models"][ai_config.alias_for(role)]["provider_model"] for role in ("publishing_planner", "publishing_metadata_writer", "publishing_hook_critic")},
            "basis": "A planning figure from the size of the input and the configured prices, with room for repair rounds (a repair call is billed again). Not a price: the cost of the run is shown after it."}


def run_view(run: PublishingPlanRun, registry: PublicationRegistry, *, with_result: bool = True) -> dict:
    result = json.loads(run.result_json or "{}")
    return {"id": run.id, "status": effective_status(run, registry.cfg), "started_at": run.started_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "progress": json.loads(run.progress_json or "[]"), "error": run.error, "usage": result.get("usage"), "applied": json.loads(run.applied_json or "{}"),
            "result": result if with_result else None}


def state(db: Session, registry: PublicationRegistry, case: Case, language: str) -> dict:
    """The AI panel of the Studio: the saved plan, a run in progress, a failure, what a new run would cost, whether the saved plan still matches the case. No model is called."""
    from app.services.case_control import global_stop_active

    scheduled = registry.cfg.language_enabled(language)
    runs = db.query(PublishingPlanRun).filter_by(case_id=case.id, language=language).order_by(PublishingPlanRun.id.desc()).limit(10).all()
    latest = next((r for r in runs if r.status == "done"), None)
    current = next((r for r in runs if effective_status(r, registry.cfg) == "running"), None)
    newest = runs[0] if runs else None
    failed = newest if newest is not None and effective_status(newest, registry.cfg) in ("failed", "interrupted") else None
    out: dict = {"language": language, "scheduled": scheduled, "latest": run_view(latest, registry) if latest else None, "running": run_view(current, registry, with_result=False) if current else None,
                 "failed": run_view(failed, registry, with_result=False) if failed else None, "emergency_stop": global_stop_active(db), "estimate": None, "stale": False, "reason": None,
                 "data_notice": registry.cfg.intelligence.scheduling.data_notice}
    if not scheduled:
        out["reason"] = f"Language '{language}' has no publishing schedule (config/publishing.json)."
        return out
    inputs = build(db, registry, case, language)
    out["estimate"] = estimate(registry, inputs) if inputs.destinations else None
    out["stale"] = bool(latest and latest.input_fingerprint != inputs.fingerprint)
    out["up_to_date"] = bool(latest and latest.input_fingerprint == inputs.fingerprint)
    out["reason"] = (None if inputs.destinations else "This language has no film and no valid ShortClip to plan.")
    return out


def start(db: Session, registry: PublicationRegistry, case: Case, language: str, *, force: bool = False) -> PublishingPlanRun:
    """Create the run (it is executed by `execute`). Refused when it would be pointless or unsafe; never called by opening a page."""
    from app.services.case_control import case_held, global_stop_active

    if not registry.cfg.language_enabled(language):
        raise PlanRunError(f"Language '{language}' has no publishing schedule (config/publishing.json).")
    if global_stop_active(db) or case_held(db, case.id):
        raise PlanRunError("The Emergency Stop (or a stop of this case) is active: no paid call may start.")
    runs = db.query(PublishingPlanRun).filter_by(case_id=case.id, language=language).order_by(PublishingPlanRun.id.desc()).limit(10).all()
    if any(effective_status(r, registry.cfg) == "running" for r in runs):
        raise PlanRunError("A publishing plan is already being generated for this language.")
    for stale in (r for r in runs if r.status == "running"):
        stale.status, stale.error = "interrupted", stale.error or "The run stopped reporting (a restart or a crash)."
    registry.plan(case.id, language)                                        # the publications exist before the model is asked: no AI in this step
    inputs = build(db, registry, case, language)
    if not inputs.destinations:
        raise PlanRunError("There is no film and no valid ShortClip to plan.")
    done = next((r for r in runs if r.status == "done"), None)
    if done is not None and done.input_fingerprint == inputs.fingerprint and not force:
        raise PlanRunError("The saved publishing plan is up to date with the case: nothing would change. Use Regenerate to run it again anyway.")
    platforms = list(dict.fromkeys(d.split("/")[0] for d in inputs.destinations))
    steps = [{"step": "analysis", "label": "Judging the Shorts, ordering them, planning the calendar", "status": "pending", "detail": ""},
             *({"step": f"metadata:{p}", "label": f"Writing the {p.title()} metadata", "status": "pending", "detail": ""} for p in platforms),
             {"step": "apply", "label": "Saving the plan into the publications", "status": "pending", "detail": ""}]
    run = PublishingPlanRun(case_id=case.id, language=language, status="running", input_fingerprint=inputs.fingerprint, progress_json=json.dumps(steps))
    db.add(run)
    db.commit()
    return run


def _progress(db: Session, run: PublishingPlanRun, step: str, status: str, detail: str = "") -> None:
    steps = json.loads(run.progress_json or "[]")
    for entry in steps:
        if entry["step"] == step:
            entry["status"], entry["detail"] = status, detail
    run.progress_json = json.dumps(steps)
    run.updated_at = utc_now()
    db.commit()


def _apply(db: Session, registry: PublicationRegistry, case: Case, language: str, inputs: Inputs, analysis: dict, metadata: dict, slots: list[SCH.Slot]) -> dict:
    zone = registry.cfg.languages[language].timezone or "UTC"
    by_slot = {(s.destination, s.asset): s for s in slots}
    order = {p: [e["clip_key"] for e in seq] for p, seq in analysis["sequences"].items()}
    applied, kept = [], []
    for pub in registry.db.query(Publication).filter_by(case_id=case.id, language=language).all():
        asset = "film" if pub.kind == "film" else pub.asset_key
        slot = by_slot.get((f"{pub.platform}/{pub.kind}", asset))
        item = (metadata.get(pub.platform, {}).get("results", {}).get(f"{pub.kind}/{asset}") or {}).get("item")
        rank = order.get(pub.platform, []).index(asset) + 1 if pub.kind == "short" and asset in order.get(pub.platform, []) else None
        notes = registry.apply_ai(pub, item, scheduled_at=SCH.to_utc(slot, zone) if slot else None, sequence=rank)
        (kept if notes else applied).append({"id": pub.id, "destination": f"{pub.platform}/{pub.kind}", "asset": asset, **({"why": "; ".join(notes)} if notes else {})})
    db.commit()
    return {"applied": [a["id"] for a in applied], "kept": kept}


async def execute(run_id: int, gen=None) -> None:
    """Run the plan: planner, then the metadata of each platform, then the calendar and the saving. Its own session; every failure is recorded on the run."""
    db = SessionLocal()
    try:
        run = db.get(PublishingPlanRun, run_id)
        case = db.get(Case, run.case_id)
        registry = PublicationRegistry(db)
        runs: list[int] = []
        try:
            inputs = build(db, registry, case, run.language)
            ai = PublishingAI(gen)
            _progress(db, run, "analysis", "running")
            analysis = await ai.analyse(db, registry, inputs, runs)
            _progress(db, run, "analysis", "done", f"{len(analysis['clips'])} clips judged")
            for p in dict.fromkeys(d.split("/")[0] for d in inputs.destinations):
                _progress(db, run, f"metadata:{p}", "running")
            metadata = await ai.write_all(registry, inputs, analysis, runs, on_done=lambda platform, made: _progress(
                db, run, f"metadata:{platform}", "failed" if made.get("error") or not made["results"] else "done",
                made.get("error") or f"{len(made['results'])} destinations" + (f"; {len(made['unresolved'])} points still wrong" if made.get("unresolved") else "")))
            if not any(m["results"] for m in metadata.values()):
                raise PlanError("no platform produced usable metadata: " + "; ".join(m.get("error", "") for m in metadata.values())[:300])
            _progress(db, run, "apply", "running")
            lang = registry.cfg.languages[run.language]
            slots = SCH.resolve(analysis["schedule_items"], plan_start=inputs.plan_start, fallback_time=lang.publish_time_local or "20:30", settings=registry.cfg.intelligence.scheduling,
                                config=registry.cfg, order={p: [e["clip_key"] for e in seq] for p, seq in analysis["sequences"].items()}, destinations=inputs.destinations,
                                existing_films=inputs.existing_films)
            applied = _apply(db, registry, case, run.language, inputs, analysis, metadata, slots)
            usage = usage_of(db, runs, case.id, run.started_at)
            run.result_json = json.dumps({
                "fingerprint": inputs.fingerprint, "plan_start": inputs.plan_start.isoformat(),
                "analysis": {k: analysis[k] for k in ("clips", "sequences", "routing", "warnings", "notes")},
                "schedule": {"slots": [{"destination": s.destination, "asset": s.asset, "local_date": s.day.isoformat(), "local_time": s.local_time, "reason": s.reason, "moved": s.moved}
                                       for s in slots], "summary": analysis["schedule_summary"], "assumptions": analysis["assumptions"],
                                "notice": registry.cfg.intelligence.scheduling.data_notice},
                "metadata": {f"{p}/{k}": v for p, made in metadata.items() for k, v in made["results"].items()},
                "platform_errors": {p: made["error"] for p, made in metadata.items() if made.get("error")},
                "unresolved": {p: made["unresolved"] for p, made in metadata.items() if made.get("unresolved")},
                "models": {"planner": analysis.get("model"), "writers": {p: made.get("model") for p, made in metadata.items()}},
                "usage": usage}, ensure_ascii=False)
            run.applied_json = json.dumps(applied)
            run.agent_run_ids_json = json.dumps(usage["agent_run_ids"])
            _progress(db, run, "apply", "done", f"{len(applied['applied'])} publications updated, {len(applied['kept'])} kept as they were")
            run.status, run.finished_at, run.error = "done", utc_now(), None
            db.commit()
        except (BudgetStopped, JobPaused) as exc:
            db.rollback()
            _fail(db, run_id, f"Stopped: {exc}" if str(exc) else "Stopped by the Emergency Stop or the case's cost cap.", runs)
        except Exception as exc:  # noqa: BLE001 - recorded on the run, shown in the Studio and on the Monitoring page
            db.rollback()
            _fail(db, run_id, f"{type(exc).__name__}: {exc}"[:800], runs)
    finally:
        db.close()


def _fail(db: Session, run_id: int, message: str, runs: list[int]) -> None:
    run = db.get(PublishingPlanRun, run_id)
    steps = json.loads(run.progress_json or "[]")
    for entry in steps:
        if entry["status"] == "running":
            entry["status"], entry["detail"] = "failed", message[:200]
    run.progress_json = json.dumps(steps)
    run.status, run.error, run.finished_at = "failed", message, utc_now()
    usage = usage_of(db, runs, run.case_id, run.started_at)
    run.result_json = json.dumps({"usage": usage})
    run.agent_run_ids_json = json.dumps(usage["agent_run_ids"])
    db.commit()


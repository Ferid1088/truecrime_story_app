"""One planning pass over real case 15 with the Main Visual Planner, the Host plan and the Detective
Toolkit, stopping before any rendering. Standalone script (not pytest); each stage is re-runnable.

    python3 -I docs/detective_toolkit_case15_replan_evidence/replan.py <stage>

Stages, in order: init, toolkit, needs, verify, research, host, direct, report.
Never run here: voice or music synthesis, avatar/host scene rendering, the production script (it splices
audio and writes usage memory), the render engine, stage_review, publishing.

Spend is bounded by a ceiling on THIS run (CEILING_EUR) on top of the case's own cap: every generation call
checks both first and stops cleanly, and a stage never starts past the ceiling.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, ".")
from sqlalchemy import text  # noqa: E402

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal, engine  # noqa: E402
from app.db.models import Case, EditorialBlueprint, StoryVersion, VisualPlan  # noqa: E402
from app.services.budget import BudgetService, BudgetStopped  # noqa: E402

CASE, BLUEPRINT, MASTER, SPOKEN = 15, 7, 240, 248
CEILING_EUR = 50.0
HERE = pathlib.Path(__file__).parent
STATE, LOG = HERE / "run_state.json", HERE / "stage_log.json"


def load(path: pathlib.Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path: pathlib.Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, default=str))


def run_spend(start_id: int) -> float:
    with engine.connect() as c:
        return float(c.execute(text("select coalesce(sum(cost_eur),0) from provider_calls "
                                    "where case_id=:c and id>:s"), {"c": CASE, "s": start_id}).scalar())


class RunGuard:
    def __init__(self, start_id: int):
        self.start_id = start_id

    def check(self, stage: str) -> None:
        spent = run_spend(self.start_id)
        if spent >= CEILING_EUR:
            raise BudgetStopped(CASE, stage, f"this run's ceiling €{CEILING_EUR:.2f} reached: €{spent:.2f}")
        with SessionLocal() as db:
            snap = BudgetService(db).snapshot(CASE)
        if snap.spent_eur >= snap.cap_eur - 1e-9:
            raise BudgetStopped(CASE, stage, f"case cap €{snap.cap_eur:.2f} reached")


def install_guard(guard: RunGuard) -> None:
    from app.providers.generation.openai_compat import OpenAICompatibleGenerationProvider as Provider

    for name in ("generate_text", "generate_structured"):
        original = getattr(Provider, name)

        async def guarded(self, *a, _original=original, **kw):
            guard.check("generation call")
            return await _original(self, *a, **kw)

        setattr(Provider, name, guarded)


def calls_between(first_id: int, last_id: int) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(text(
            "select coalesce(stage,'-'), coalesce(model,'-'), coalesce(host,'-'), count(*), "
            "coalesce(sum(cost_eur),0), coalesce(sum(tokens_in),0), coalesce(sum(tokens_out),0), "
            "count(*) filter (where estimated) from provider_calls "
            "where case_id=:c and id>:a and id<=:b group by 1,2,3 order by 5 desc"),
            {"c": CASE, "a": first_id, "b": last_id}).fetchall()
    return [{"stage": r[0], "model": r[1], "host": r[2], "calls": r[3], "eur": round(float(r[4]), 4),
             "tokens_in": int(r[5]), "tokens_out": int(r[6]), "estimated_rows": r[7]} for r in rows]


def max_call_id() -> int:
    with engine.connect() as c:
        return int(c.execute(text("select coalesce(max(id),0) from provider_calls")).scalar())


def counts(db, graph_id: int) -> dict:
    q = lambda sql, **p: db.execute(text(sql), p).scalar()  # noqa: E731
    return {
        "narration_units": q("select count(*) from narration_units where graph_id=:g", g=graph_id),
        "scenes_by_mode": {r[0]: r[1] for r in db.execute(text(
            "select mode, count(*) from detective_scenes where graph_id=:g group by 1"), {"g": graph_id})},
        "actions_by_tool": {r[0]: r[1] for r in db.execute(text(
            "select ta.tool_id, count(*) from detective_tool_actions ta join detective_scenes s on s.id=ta.scene_id "
            "where s.graph_id=:g group by 1 order by 2 desc"), {"g": graph_id})},
        "claims": q("select count(*) from detective_claims where graph_id=:g", g=graph_id),
        "questions": q("select count(*) from detective_questions where graph_id=:g", g=graph_id),
        "relations": q("select count(*) from detective_relations where graph_id=:g", g=graph_id),
        "knowledge_events": q("select count(*) from detective_knowledge_events where graph_id=:g", g=graph_id),
    }


async def s_toolkit(db, state):
    from app.documentary.detective import toolkit as DT
    from app.documentary.mvp import pipeline as MVP
    from dataclasses import asdict

    case, bp = db.get(Case, CASE), db.get(EditorialBlueprint, BLUEPRINT)
    graph = DT.ensure_graph(db, case, bp)
    ai_config.detective_toolkit.enabled = True
    result = await DT.invoke(db, case, bp, graph)
    db.commit()
    return {**asdict(result), "graph_id": graph.id, **counts(db, graph.id)}


async def s_needs(db, state):
    from app.documentary.mvp import pipeline as MVP

    if state.get("needs_plan_id"):
        return {"already_planned_plan_id": state["needs_plan_id"]}
    case, bp = db.get(Case, CASE), db.get(EditorialBlueprint, BLUEPRINT)
    ai_config.detective_toolkit.enabled = True
    plan, graph, detail = await MVP.stage_needs(db, case, bp, replan=True)
    state["needs_plan_id"] = plan.id
    return {**detail, "plan_id": plan.id, "version": plan.version, **counts(db, graph.id)}


async def s_verify(db, state):
    from app.documentary.mvp import pipeline as MVP

    plan = db.get(VisualPlan, state["needs_plan_id"])
    return await MVP.stage_library_and_verify(db, db.get(Case, CASE), plan)


async def s_research(db, state):
    from app.documentary.mvp import pipeline as MVP

    plan = db.get(VisualPlan, state["needs_plan_id"])
    return await MVP.stage_research(db, db.get(Case, CASE), plan)


async def s_host(db, state):
    """Report only: the Host plan already exists and stays the Host system's. Nothing is rendered or called."""
    from app.db.models import HostScene
    from app.documentary.host import latest_host_plan, latest_host_segments

    bp, version = db.get(EditorialBlueprint, BLUEPRINT), db.get(StoryVersion, SPOKEN)
    row = latest_host_plan(db, bp.id)
    plan = json.loads(row.plan_json or "{}") if row else {}
    seg = latest_host_segments(db, version.id)
    scenes = db.query(HostScene).filter(HostScene.host_segments_id == seg.id).order_by(HostScene.id).all() if seg else []
    columns = [c.name for c in HostScene.__table__.columns]
    keep = [c for c in ("id", "position", "beat_id", "status", "seconds", "duration_seconds", "outfit", "avatar_video_path",
                        "voice_status", "avatar_status") if c in columns]
    return {"host_plan_id": row.id if row else None, "host_plan_status": row.status if row else None,
            "segments": [{k: s.get(k) for k in ("id", "position", "beat_id")} for s in plan.get("segments") or []],
            "segments_row_id": seg.id if seg else None,
            "scenes": [{k: getattr(s, k) for k in keep} for s in scenes], "scene_columns": columns}


async def s_direct(db, state):
    from app.documentary.jobs import _load_manifest
    from app.documentary.mvp import pipeline as MVP
    from app.documentary.pipeline.errors import AwaitingPictureReview

    case, bp, version = db.get(Case, CASE), db.get(EditorialBlueprint, BLUEPRINT), db.get(StoryVersion, SPOKEN)
    needs_plan = db.get(VisualPlan, state["needs_plan_id"])
    ai_config.detective_toolkit.enabled = True
    manifest = _load_manifest(CASE, version)
    plan_row, report = await MVP.stage_direct(db, case, needs_plan, bp, manifest, "en")
    state["language_plan_id"] = plan_row.id
    out = {"language_plan_id": plan_row.id}
    for key in ("shots", "gap_seconds", "agent_directed_beats", "assets_used", "alternatives", "alternatives_unplaced",
                "neighbour_cover", "reuse", "rounds"):
        value = report.get(key)
        out[key] = value if isinstance(value, (int, float, str, type(None), dict)) else (len(value) if value is not None else None)
    out["gaps"] = len(report.get("gaps") or [])
    try:
        out["complete"] = MVP.stage_complete(plan_row, "en")
    except AwaitingPictureReview as e:
        out["complete"] = {"awaiting_picture_review": str(e)[:300]}
    return out


STAGES = {"toolkit": s_toolkit, "needs": s_needs, "verify": s_verify, "research": s_research, "host": s_host,
          "direct": s_direct}


async def run_stage(name: str) -> bool:
    state = load(STATE, None)
    if state is None:
        raise SystemExit("run init first")
    guard = RunGuard(state["start_id"])
    install_guard(guard)
    first, started = max_call_id(), time.time()
    entry = {"stage": name, "started": datetime.now(timezone.utc).isoformat(), "first_call_id_exclusive": first}
    with SessionLocal() as db:
        try:
            guard.check(name)
            entry["detail"] = await STAGES[name](db, state)
            db.commit()
            entry["ok"] = True
        except BudgetStopped as e:
            db.rollback()
            entry.update(ok=False, stopped=str(e.reason))
        except Exception as e:  # noqa: BLE001 - logged in full, then the loop fixes the root cause
            db.rollback()
            entry.update(ok=False, error=f"{type(e).__name__}: {e}"[:600], trace=traceback.format_exc()[-2200:])
    last = max_call_id()
    entry.update(seconds=round(time.time() - started, 1), last_call_id=last, calls=calls_between(first, last))
    entry["stage_eur"] = round(sum(c["eur"] for c in entry["calls"]), 4)
    entry["run_eur_so_far"] = round(run_spend(state["start_id"]), 4)
    save(STATE, state)
    log = load(LOG, [])
    log.append(entry)
    save(LOG, log)
    print(json.dumps({k: entry[k] for k in entry if k not in ("trace",)}, indent=1, default=str)[:6000])
    if not entry["ok"]:
        print(entry.get("trace", ""))
    return entry["ok"]


def init() -> None:
    with SessionLocal() as db:
        snap = BudgetService(db).snapshot(CASE)
    save(STATE, {"start_id": max_call_id(), "ceiling_eur": CEILING_EUR,
                 "started": datetime.now(timezone.utc).isoformat(),
                 "budget_before": {"cap": snap.cap_eur, "spent": snap.spent_eur, "remaining": snap.remaining_eur}})
    save(LOG, [])
    print(load(STATE, {}))


if __name__ == "__main__":
    stage = sys.argv[1]
    if stage == "init":
        init()
    else:
        sys.exit(0 if asyncio.run(run_stage(stage)) else 1)

"""The visual funnel of a case (read-only): where its pictures and videos were lost between
the search and the finished film, and which cap or rule was hit.

Stages: found (stored with a file) -> checked (vision verdict) -> offered to the director per
beat -> used in a film. Nothing here calls a provider; every number comes from the database and
from replaying the director's own candidate ranking on the stored plan."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    DocumentaryJob,
    EditorialBlueprint,
    MediaUsage,
    ProductionScript,
    VideoSource,
    VisualAsset,
    VisualPlan,
)
from app.documentary.visuals import director as D
from app.documentary.visuals import rights as R

OTHER = "other"
UNVERIFIED = "unverified"
GENERATED = "generated"
RESEARCH_STAGES = ("visual_research", "visual_gaps", "visual_check")
# Characters of a shot's reason that name its fill way in the report.
WAY_LABEL_CHARS = 24


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def rejection_reason(asset: VisualAsset) -> str:
    """Why a rejected picture was rejected: the verdict's reason, one of the configured labels,
    else "other"."""
    reason = _loads(asset.verification_json, {}).get("reason")
    return reason if reason in ai_config.visual_report_settings().rejection_reasons else OTHER


def provider_funnel(db: Session, case_id: int) -> list[dict]:
    """One row per (found_during, provider, asset type): stored, with a file, and the vision
    verdicts; rejected pictures broken down by reason."""
    rows: dict[tuple, dict] = {}
    for a in db.query(VisualAsset).filter(VisualAsset.case_id == case_id).all():
        if a.asset_type == "video_source":
            continue
        key = (a.found_during or "", a.provider, a.asset_type)
        row = rows.setdefault(key, {
            "stage": key[0], "provider": key[1], "type": key[2], "stored": 0, "downloaded": 0,
            "verified": 0, "needs_review": 0, "rejected": 0, "unverified": 0,
            "rejected_by": Counter()})
        row["stored"] += 1
        row["downloaded"] += bool(a.local_path)
        status = a.verification_status if a.verification_status in (
            "verified", "needs_review", "rejected") else UNVERIFIED
        row[status] += 1
        if status == "rejected":
            row["rejected_by"][rejection_reason(a)] += 1
    return sorted(rows.values(), key=lambda r: (r["stage"], r["provider"], r["type"]))


def intake_stages(db: Session, case_id: int) -> list[dict]:
    """What the research stages of each job reported: queries, candidates, additions, the
    candidates dropped before storage, and the supply loop."""
    out = []
    for job in db.query(DocumentaryJob).filter(DocumentaryJob.case_id == case_id).order_by(
            DocumentaryJob.id):
        for stage in _loads(job.stages_json, []):
            if stage.get("name") in RESEARCH_STAGES and stage.get("status") not in ("skipped", "pending"):
                detail = stage.get("detail")
                out.append({"job": job.id, "stage": stage["name"], "status": stage.get("status"),
                            "detail": detail if isinstance(detail, dict) else {"text": detail}})
    return out


def _plan_context(db: Session, plan: VisualPlan) -> tuple[Case, EditorialBlueprint, dict, dict]:
    case, row = db.get(Case, plan.case_id), db.get(EditorialBlueprint, plan.blueprint_id)
    assert case is not None and row is not None, "a visual plan belongs to a case and a blueprint"
    return case, row, _loads(row.blueprint_json, {}), _loads(plan.requirements_json, {})


def beat_offers(db: Session, plan: VisualPlan, profile: str | None = None) -> list[dict]:
    """Per beat: the candidates the director is offered now (replayed), those stored with the
    plan, how many of each tier, and how many pictures the film shows there."""
    case, row, blueprint, requirements = _plan_context(db, plan)
    marks = D.beat_marks(db, row, blueprint)
    offered = D.beat_candidates(db, case, blueprint, requirements, profile, marks)
    stored = _loads(plan.plan_json, {}).get("candidates") or {}
    used = defaultdict(set)
    for u in db.query(MediaUsage).filter(MediaUsage.case_id == case.id,
                                         MediaUsage.film_key == f"bp{plan.blueprint_id}"):
        used[u.beat_id].add(u.asset_id)
    out = []
    for beat in blueprint.get("beats") or []:
        bid = beat["id"]
        out.append({"beat": bid, "seconds": round(D.beat_seconds(beat), 1),
                    "offered_now": len(offered.get(bid, [])), "stored_with_plan": len(stored.get(bid, [])),
                    "tiers": dict(Counter(D.asset_tier(a) for _, a in offered.get(bid, []))),
                    "used": len(used.get(bid, ()))})
    return out


def _excluded_because(a: VisualAsset, entity: dict, req: dict, blocked: set[str],
                      profile: str | None) -> str | None:
    """The first rule that keeps a stored picture away from a requirement, else None."""
    if a.asset_type in ("map", "document", "card") and a.provider == GENERATED:
        return "generated_card"
    if a.asset_type == "video_source":
        return "whole_video"
    if a.verification_status == "rejected":
        return "rejected"
    if a.local_path is None:
        return "no_file"
    if not R.usable(a, profile):
        return "rights"
    if not D.firewall_ok(a, blocked):
        return "reveal_firewall"
    if a.asset_role not in req["acceptable_roles"]:
        return "role"
    if D.entity_score(a, entity) <= 0:
        return "not_about_the_entity"
    return None


def empty_beat_reasons(db: Session, plan: VisualPlan, beat_id: str,
                       profile: str | None = None) -> dict[str, dict]:
    """For every entity a beat needs: how many stored pictures were kept away by which rule
    (the first rule that fires), and how many ranked."""
    _, _, blueprint, requirements = _plan_context(db, plan)
    entities = {e["key"]: e for e in requirements.get("entities") or []}
    reqs: dict = next((b for b in requirements.get("beats") or [] if b["beat_id"] == beat_id), {})
    blocked = D.blocked_at(blueprint, beat_id)
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == plan.case_id).all()
    out = {}
    for r in reqs.get("requirements") or []:
        entity = entities.get(r["entity"])
        if entity is None:
            continue
        loose = dict(r, acceptable_roles=["evidence", "context", "illustration"])
        why = Counter(_excluded_because(a, entity, loose, blocked, profile) or "ranked"
                      for a in assets)
        out[r["entity"]] = {"purpose": r.get("purpose"), "name": entity["name"], **why}
    return out


def film_stats(db: Session, script: ProductionScript) -> dict:
    """What a finished film shows: seconds and shots per kind of picture, the real pictures
    (not generated) with their distinct count and average shot length."""
    shots = _loads(script.script_json, {}).get("shots") or []
    assets = {a.asset_code: a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == script.case_id)}
    by_kind: dict[str, dict] = defaultdict(lambda: {"shots": 0, "seconds": 0.0})
    real_images: list[dict] = []
    real_videos: list[dict] = []
    for s in shots:
        a = assets.get(s.get("asset_id") or "")
        generated = a is not None and a.provider == GENERATED
        kind = s.get("kind") or "unknown"
        real = kind == "video" or (a is not None and not generated)
        label = ("illustration" if generated and s.get("role") == "illustration" else
                 f"generated {kind}" if generated else f"real {kind}" if real else kind)
        by_kind[label]["shots"] += 1
        by_kind[label]["seconds"] += s["end"] - s["start"]
        if real:
            (real_videos if kind == "video" else real_images).append(s)
    seconds = [s["end"] - s["start"] for s in real_images]
    return {"duration": script.duration_seconds, "shots": len(shots),
            "by_kind": {k: {"shots": v["shots"], "seconds": round(v["seconds"], 1)}
                        for k, v in sorted(by_kind.items())},
            "real_image_shots": len(real_images),
            "real_distinct": len({s["asset_id"] for s in real_images}),
            "real_avg_seconds": round(sum(seconds) / len(seconds), 1) if seconds else 0.0,
            "video_shots": len(real_videos),
            "video_seconds": round(sum(s["end"] - s["start"] for s in real_videos), 1)}


def plan_rules(plan: VisualPlan) -> dict[str, Counter]:
    """How often the director's validator dropped or changed a shot, by rule, and the warnings
    it raised, by code (density, one shot per sentence, firewall, not-a-candidate ...)."""
    validation = _loads(plan.validation_json, {}).get("plan") or {}
    adjustments = Counter(a.get("reason") or next(k for k in a if k != "beat")
                          for a in validation.get("adjustments") or [])
    warnings = Counter(w.get("code") for w in validation.get("warnings") or [])
    return {"adjustments": adjustments, "warnings": warnings}


def stage_details(db: Session, case_id: int, name: str) -> list[dict]:
    """The detail of every run of one job stage (coverage repair, render ...), newest last."""
    return [{"job": job.id, **(stage.get("detail") if isinstance(stage.get("detail"), dict) else {})}
            for job in db.query(DocumentaryJob).filter(DocumentaryJob.case_id == case_id).order_by(
                DocumentaryJob.id)
            for stage in _loads(job.stages_json, [])
            if str(stage.get("name", "")).split(":")[0] == name and stage.get("status") != "pending"]


def fill_ways_used(script: ProductionScript) -> Counter:
    """Shots of a finished film made by each fill way (reframe, split, board, text) and by the
    coverage repair."""
    shots = _loads(script.script_json, {}).get("shots") or []
    return Counter((s.get("fill_reason") or s.get("why") or "")[:WAY_LABEL_CHARS] for s in shots
                   if s.get("fill_reason") or str(s.get("why", "")).startswith("coverage repair"))


def video_funnel(db: Session, case_id: int) -> dict:
    """Videos found as sources (film candidate or research only, and why) and the clips and
    pieces stored from them."""
    sources = db.query(VideoSource).filter(VideoSource.case_id == case_id).all()
    by_use = Counter((v.use, v.use_reason or "") for v in sources)
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == case_id,
                                          VisualAsset.asset_type.in_(("video", "video_source"))).all()
    pieces = [a for a in assets if a.asset_type == "video" and "parent" in (a.spec_json or "")]
    return {"found": len(sources),
            "by_use": [{"use": u, "reason": r, "videos": n} for (u, r), n in by_use.most_common()],
            "by_class": dict(Counter(v.source_class or "none" for v in sources)),
            "usable_cuts": sum(a.verification_status == "verified" for a in assets if a.asset_type == "video"),
            "pieces": len(pieces),
            "clip_assets": Counter(a.asset_type for a in assets),
            "clips_by_status": Counter(a.verification_status for a in assets
                                       if a.asset_type == "video"),
            "downloaded": sum(bool(a.local_path) for a in assets if a.asset_type == "video")}


def caps_hit(db: Session, case_id: int, plan: VisualPlan) -> list[dict]:
    """Every cap or rule of the picture pipeline with the value the case reached."""
    vs, vd = ai_config.visual_search, ai_config.visual_direction
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == case_id).all()
    live = sum(a.verification_status != "rejected" and a.provider != GENERATED for a in assets)
    requests = len(_loads(plan.plan_json, {}).get("search_requests") or [])
    per_req_overflow = _requirements_over_cap(db, plan)
    return [
        {"cap": "visual_search.max_assets", "limit": vs.max_assets, "reached": live,
         "hit": live >= vs.max_assets},
        {"cap": "visual_search.max_queries", "limit": vs.max_queries, "reached": None, "hit": None},
        {"cap": "supply.max_searches", "limit": ai_config.supply.max_searches,
         "reached": _supply_searches(db, case_id), "hit": None},
        {"cap": "visual_direction.max_search_requests", "limit": vd.max_search_requests,
         "reached": requests, "hit": requests >= vd.max_search_requests},
        {"cap": "director candidates per requirement", "limit": ai_config.visual_direction.candidates_per_requirement,
         "reached": per_req_overflow, "hit": per_req_overflow > 0},
        {"cap": "visual_direction.max_*_appearances", "limit": vd.max_evidence_appearances,
         "reached": None, "hit": None},
        {"cap": "footage.max_pieces_per_source", "limit": ai_config.footage.max_pieces_per_source,
         "reached": None, "hit": None},
        {"cap": "coverage.max_still_seconds", "limit": ai_config.coverage.max_still_seconds,
         "reached": None, "hit": None},
    ]


def _supply_searches(db: Session, case_id: int) -> int | None:
    runs = [s["detail"].get("supply", {}).get("searches") for s in intake_stages(db, case_id)
            if s["stage"] == "visual_check"]
    runs = [r for r in runs if r is not None]
    return max(runs) if runs else None


def _requirements_over_cap(db: Session, plan: VisualPlan) -> int:
    """Requirements that had more ranked pictures than the director is shown."""
    case, _, blueprint, requirements = _plan_context(db, plan)
    entities = {e["key"]: e for e in requirements.get("entities") or []}
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
    cap = ai_config.visual_direction.candidates_per_requirement
    over = 0
    for beat in requirements.get("beats") or []:
        blocked = D.blocked_at(blueprint, beat["beat_id"])
        for r in beat["requirements"]:
            ent = entities.get(r["entity"])
            if ent and len(D.rank_candidates(r, ent, assets, blocked)) > cap:
                over += 1
    return over

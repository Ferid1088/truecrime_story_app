"""One episode-level release plan for all selected short concepts and destinations.

The plan is intentionally separate from the publisher adapters. It can be reviewed
and saved without starting or interrupting the documentary/short production workers.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import (
    AutopilotRun,
    Case,
    DocumentaryJob,
    RenderedShort,
    ShortCandidate,
    ShortFormPublishingPlan,
)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "social_distribution.json"
PLATFORM_LABELS = {
    "youtube_short": "YouTube Shorts",
    "instagram_reel": "Instagram Reels",
    "facebook_reel": "Facebook Reels",
    "tiktok_video": "TikTok",
}


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    campaign = config.get("campaign") or {}
    ZoneInfo(campaign.get("timezone", "UTC"))
    schedule = config.get("schedule_template") or []
    if not schedule:
        raise ValueError("social_distribution.schedule_template must not be empty")
    for slot in schedule:
        if int(slot.get("day", 0)) < 1 or int(slot.get("short", 0)) < 1:
            raise ValueError("Schedule days and short numbers must be positive")
        if any(platform not in PLATFORM_LABELS for platform in slot.get("platforms", [])):
            raise ValueError("Schedule contains an unknown platform")
    return config


def _latest_job(db: Session, case_id: int) -> DocumentaryJob | None:
    return (db.query(DocumentaryJob)
            .filter(DocumentaryJob.case_id == case_id,
                    DocumentaryJob.status.in_(("completed", "completed_warnings")))
            .order_by(DocumentaryJob.id.desc()).first())


def _campaign_jobs(db: Session, anchor: DocumentaryJob) -> list[DocumentaryJob]:
    """Collect finished per-language films belonging to the same Autopilot run."""
    runs = (db.query(AutopilotRun)
            .filter(AutopilotRun.case_id == anchor.case_id)
            .order_by(AutopilotRun.id.desc()).all())
    for run in runs:
        try:
            job_ids = {int(value) for value in json.loads(run.film_job_ids_json or "{}").values()}
        except (TypeError, ValueError):
            continue
        if anchor.id not in job_ids:
            continue
        jobs = (db.query(DocumentaryJob)
                .filter(DocumentaryJob.id.in_(job_ids),
                        DocumentaryJob.case_id == anchor.case_id,
                        DocumentaryJob.status.in_(("completed", "completed_warnings")))
                .order_by(DocumentaryJob.id).all())
        if jobs:
            return jobs
    if anchor.batch_id:
        jobs = (db.query(DocumentaryJob)
                .filter(DocumentaryJob.case_id == anchor.case_id,
                        DocumentaryJob.batch_id == anchor.batch_id,
                        DocumentaryJob.status.in_(("completed", "completed_warnings")))
                .order_by(DocumentaryJob.id).all())
        if jobs:
            return jobs
    return [anchor]


def build_release_document(db: Session, case_id: int, *,
                           included_candidate_ids: set[int] | None = None,
                           start_date: date | None = None) -> dict:
    config = load_config()
    anchor = _latest_job(db, case_id)
    case = db.get(Case, case_id)
    if anchor is None or case is None:
        raise ValueError("A finished film is required before creating a release plan")

    jobs = _campaign_jobs(db, anchor)
    job_ids = [job.id for job in jobs]
    candidates = (db.query(ShortCandidate)
                  .filter(ShortCandidate.film_job_id.in_(job_ids))
                  .order_by(ShortCandidate.source_start_seconds, ShortCandidate.id).all())
    candidate_ids = [row.id for row in candidates]
    renders = (db.query(RenderedShort)
               .filter(RenderedShort.film_job_id.in_(job_ids),
                       RenderedShort.candidate_id.in_(candidate_ids or [-1]))
               .order_by(RenderedShort.candidate_id, RenderedShort.language).all())
    by_candidate: dict[int, list[RenderedShort]] = {}
    for render in renders:
        by_candidate.setdefault(render.candidate_id, []).append(render)

    groups: dict[str, list[ShortCandidate]] = {}
    for candidate in candidates:
        if by_candidate.get(candidate.id):
            groups.setdefault(candidate.stable_key, []).append(candidate)
    eligible = [rows for rows in groups.values() if rows]
    eligible.sort(key=lambda rows: (rows[0].source_start_seconds or 0, rows[0].id))
    included_keys = (
        {key for key, rows in groups.items()
         if any(row.id in included_candidate_ids for row in rows)}
        if included_candidate_ids is not None else
        {key for key, rows in groups.items()
         if any(row.status == "selected" for row in rows)
         or config["campaign"].get("selected_by_default", True)}
    )
    review_language = config["campaign"].get("review_language", "en")
    render_maps: dict[str, dict[str, RenderedShort]] = {}
    representatives: dict[str, ShortCandidate] = {}
    candidate_items = []
    for rows in eligible:
        key = rows[0].stable_key
        language_renders: dict[str, RenderedShort] = {}
        for candidate in rows:
            for render in by_candidate[candidate.id]:
                current = language_renders.get(render.language)
                if current is None or render.id > current.id:
                    language_renders[render.language] = render
        representative = next((row for row in rows if row.language == review_language), rows[0])
        render_maps[key] = language_renders
        representatives[key] = representative
        candidate_items.append({
            "id": representative.id,
            "stable_key": key,
            "hook": representative.hook,
            "status": representative.status,
            "included": key in included_keys,
            "languages": [{"language": language, "render_id": render.id,
                           "render_status": render.status,
                           "approval_state": render.approval_state,
                           "video_url": f"/api/short-form/files/{render.id}/video"
                           if render.video_path else None}
                          for language, render in sorted(language_renders.items())],
        })

    timezone = ZoneInfo(config["campaign"].get("timezone", "UTC"))
    clock = time.fromisoformat(config["campaign"].get("publish_time_local", "18:00"))
    now_local = datetime.now(timezone)
    base_date = start_date or (now_local.date() + timedelta(
        days=int(config["campaign"].get("start_offset_days", 0))))
    if base_date < now_local.date():
        raise ValueError("Release plan start date cannot be in the past")
    if base_date == now_local.date() and now_local.time() >= clock:
        base_date += timedelta(days=1)
    platforms = config.get("platforms", {})
    accounts = config.get("accounts", {})
    template = config["schedule_template"]
    candidate_by_short = {index: rows[0].stable_key for index, rows in enumerate(eligible, start=1)
                          if rows[0].stable_key in included_keys}
    slots = []
    for slot in template:
        group_key = candidate_by_short.get(int(slot["short"]))
        if group_key is None:
            continue
        representative = representatives[group_key]
        language_renders = render_maps[group_key]
        publish_date = base_date + timedelta(days=int(slot["day"]) - 1)
        publish_at = datetime.combine(publish_date, clock, tzinfo=timezone)
        for language, render in sorted(language_renders.items()):
            for platform in slot.get("platforms", []):
                if not platforms.get(platform, {}).get("enabled", False):
                    continue
                account = (accounts.get(platform) or {}).get(language) or {}
                if render is None or render.status != "done":
                    slot_status = "render_not_ready"
                elif account.get("credential_ref"):
                    slot_status = "configured"
                else:
                    slot_status = "account_not_connected"
                slots.append({
                    "slot_key": f"{anchor.id}:{group_key}:{language}:{platform}:{publish_at.isoformat()}",
                    "day": int(slot["day"]),
                    "publish_at": publish_at.isoformat(),
                    "candidate_id": representative.id,
                    "short_number": int(slot["short"]),
                    "language": language,
                    "platform": platform,
                    "platform_label": PLATFORM_LABELS[platform],
                    "render_id": render.id if render else None,
                    "render_status": render.status if render else "missing",
                    "account_id": account.get("account_id") or None,
                    "channel_url": account.get("channel_url") or None,
                    "credential_ref": account.get("credential_ref") or None,
                    "status": slot_status,
                })

    return {
        "case_id": case_id,
        "case_title": case.canonical_title,
        "film_job_id": anchor.id,
        "film_job_ids": job_ids,
        "timezone": str(timezone),
        "start_date": base_date.isoformat(),
        "publish_time_local": clock.isoformat(timespec="minutes"),
        "review_language": review_language,
        "accounts_configured": bool(slots) and all(s["credential_ref"] for s in slots),
        "can_publish": False,
        "candidates": candidate_items,
        "slots": slots,
        "connected_accounts": sum(bool(slot["credential_ref"]) for slot in slots),
        "required_accounts": len(slots),
        "publisher_status": "not_implemented",
    }


def latest_plan(db: Session, case_id: int,
                film_job_id: int | None = None) -> ShortFormPublishingPlan | None:
    query = db.query(ShortFormPublishingPlan).filter(ShortFormPublishingPlan.case_id == case_id)
    if film_job_id is not None:
        query = query.filter(ShortFormPublishingPlan.source_episode_id == film_job_id)
    return (query
            .order_by(ShortFormPublishingPlan.id.desc()).first())


def store_draft(db: Session, case_id: int, document: dict) -> ShortFormPublishingPlan:
    row = latest_plan(db, case_id, document["film_job_id"])
    if row is not None and row.status in {"draft", "approved"}:
        row.source_episode_id = document["film_job_id"]
        row.settings_json = json.dumps({"timezone": document["timezone"],
                                        "start_date": document["start_date"],
                                        "publish_time_local": document["publish_time_local"]},
                                       ensure_ascii=False, sort_keys=True)
        row.plan_json = json.dumps(document, ensure_ascii=False, sort_keys=True)
        row.status = "draft"
    else:
        row = ShortFormPublishingPlan(
            case_id=case_id,
            source_episode_id=document["film_job_id"],
            settings_json=json.dumps({"timezone": document["timezone"],
                                      "start_date": document["start_date"],
                                      "publish_time_local": document["publish_time_local"]},
                                     ensure_ascii=False, sort_keys=True),
            plan_json=json.dumps(document, ensure_ascii=False, sort_keys=True),
            status="draft",
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def serialize_plan(row: ShortFormPublishingPlan) -> dict:
    return {"id": row.id, "status": row.status,
            "document": json.loads(row.plan_json or "{}"),
            "created_at": row.created_at.isoformat() if row.created_at else None}

"""Follow-up videos: a case we covered while UNSOLVED has been solved.

The monitor creates a FollowUpCandidate; the dashboard asks the user
"Do you want to create an update video?". NOTHING is produced before the
user approves. Approval starts one DocumentaryJob of production_type
"follow_up" linked to the candidate: research refreshes the new
developments, the master story opens with the earlier coverage (a
deterministic intro the director cannot drop), and the resulting Video
points to the original video.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, CaseStatusCheck, DocumentaryJob, FollowUpCandidate, Video
from app.lifecycle.status import SOLVED, UNSOLVED
from app.lifecycle.videos import covered_video, video_dict
from app.utils import utc_now

OPEN_STATES = ("pending", "approved", "in_production")


def create_candidate(db: Session, case: Case, *, previous_status: str, new_status: str,
                     check: CaseStatusCheck | None, development: str | None,
                     sources: list, confidence: float | None) -> FollowUpCandidate | None:
    """Only for a case the channel has covered (it has a Video) that went
    from UNSOLVED to SOLVED; never twice while one is open."""
    if not (previous_status == UNSOLVED and new_status == SOLVED):
        return None
    original = covered_video(db, case.id)
    if original is None:
        return None
    if db.query(FollowUpCandidate).filter(FollowUpCandidate.case_id == case.id,
                                          FollowUpCandidate.state.in_(OPEN_STATES)).first():
        return None
    fu = FollowUpCandidate(
        case_id=case.id, original_video_id=original.id,
        status_check_id=check.id if check else None, previous_status=previous_status,
        new_status=new_status, development=(development or "")[:4000] or None,
        sources_json=json.dumps(sources or [], ensure_ascii=False), confidence=confidence)
    db.add(fu)
    db.commit()
    db.refresh(fu)
    return fu


def approve(db: Session, fu: FollowUpCandidate, *, mode: str = "pilot",
            languages: list[str] | None = None, profile: str = "preview",
            pilot_seconds: float | None = None, launch: bool = True) -> DocumentaryJob:
    """The user's explicit yes: one follow-up production job."""
    from app.documentary import jobs as J

    if fu.state not in ("pending",):
        raise ValueError(f"follow-up {fu.id} is {fu.state}, not pending")
    case = db.get(Case, fu.case_id)
    original = db.get(Video, fu.original_video_id) if fu.original_video_id else None
    langs = languages or [original.language if original else "en"]
    job = J.create_job(db, case, None, langs, mode, pilot_seconds, profile,
                       refresh_visuals=True, from_zero=True)
    job.production_type = "follow_up"
    job.follow_up_id = fu.id
    fu.state = "in_production"
    fu.decided_at = utc_now()
    fu.follow_up_job_id = job.id
    db.commit()
    db.refresh(job)
    if launch:
        J.launch(job.id)
    return job


def dismiss(db: Session, fu: FollowUpCandidate) -> FollowUpCandidate:
    if fu.state != "pending":
        raise ValueError(f"follow-up {fu.id} is {fu.state}, not pending")
    fu.state = "dismissed"
    fu.decided_at = utc_now()
    db.commit()
    return fu


async def start_update_research(db: Session, case: Case, follow_up_id: int | None):
    """A targeted research job on what changed since the original video
    (arrest, charges, trial, verdict, confession, identification)."""
    from app.providers import get_research_provider
    from app.services.research_jobs import create_job

    fu = db.get(FollowUpCandidate, follow_up_id) if follow_up_id else None
    since = None
    if fu and fu.original_video_id:
        original = db.get(Video, fu.original_video_id)
        since = (original.published_at or original.created_at) if original else None
    objective = (
        "UPDATE research — the case was covered before while UNSOLVED and has since been "
        f"solved{f' (reported: {fu.development})' if fu and fu.development else ''}. Do NOT "
        "repeat the background. Find what happened "
        f"{'since ' + since.date().isoformat() if since else 'recently'}: arrest, charges, "
        "trial, verdict, confession, identification of the perpetrator, official statements "
        "of police, prosecutors and courts, and what this explains about the open questions "
        "of the earlier coverage. Primary and credible sources with full text.")
    provider = get_research_provider()
    job = create_job(db, job_type="research", case_id=case.id, input_data={
        "case_title": case.canonical_title, "language": case.language,
        "follow_up": True, "follow_up_id": follow_up_id})
    try:
        external_id = await provider.start_case_research(
            case_title=case.canonical_title, language=case.language, objective=objective,
            research_languages=ai_config.multilingual.research_languages)
    except Exception as e:
        job.status = "failed"
        job.error = str(e)[:500]
        job.completed_at = utc_now()
        db.commit()
        raise
    job.external_job_id = external_id
    job.status = "running"
    job.started_at = utc_now()
    db.commit()
    db.refresh(job)
    return job


def context(db: Session, fu: FollowUpCandidate) -> dict:
    """What the story knows about the earlier coverage."""
    case = db.get(Case, fu.case_id)
    original = db.get(Video, fu.original_video_id) if fu.original_video_id else None
    ctx = {
        "follow_up_id": fu.id,
        "case": case.canonical_title,
        "original_title": (original.youtube_title or original.title) if original else
        case.canonical_title,
        "episode_number": original.episode_number if original else None,
        "original_published": (original.published_at.date().isoformat()
                               if original and original.published_at else None),
        "previous_status": fu.previous_status, "current_status": fu.new_status,
        "development": fu.development,
        "sources": json.loads(fu.sources_json or "[]"),
    }
    ctx["intro"] = intro(ctx)
    return ctx


def intro(ctx: dict) -> str:
    """The opening sentences of every follow-up film (deterministic)."""
    ep = f" in Episode {ctx['episode_number']}" if ctx.get("episode_number") else " before"
    pub = f", published {ctx['original_published']}" if ctx.get("original_published") else ""
    return ai_config.youtube_metadata.follow_up_intro.format(
        episode=ep, original_title=ctx.get("original_title") or ctx.get("case"),
        published=pub)


def ensure_intro(text: str, ctx: dict) -> tuple[str, bool]:
    """(text, inserted). The follow-up master must open by referencing
    the earlier video; when the writer did not, the intro is put first."""
    from app.lifecycle.identity import fold, tokens

    head = fold(" ".join(text.split()[:90]))
    title_words = tokens(ctx.get("original_title"))
    mentions_video = bool(title_words) and sum(1 for w in title_words if w in head) >= max(
        1, len(title_words) * 2 // 3)
    says_solved = any(w in head for w in ("solved", "resolved", "geloest", "gelost"))
    if mentions_video and says_solved:
        return text, False
    return intro(ctx) + "\n\n" + text.lstrip(), True


def candidate_dict(db: Session, fu: FollowUpCandidate) -> dict:
    case = db.get(Case, fu.case_id)
    original = db.get(Video, fu.original_video_id) if fu.original_video_id else None
    check = db.get(CaseStatusCheck, fu.status_check_id) if fu.status_check_id else None
    return {
        "id": fu.id, "case_id": fu.case_id, "case_title": case.canonical_title if case else None,
        "state": fu.state, "previous_status": fu.previous_status, "new_status": fu.new_status,
        "development": fu.development, "confidence": fu.confidence,
        "sources": json.loads(fu.sources_json or "[]"),
        "original_video": video_dict(original) if original else None,
        "status_check": {"id": check.id, "reason": check.reason, "created_at": check.created_at}
        if check else None,
        "question": ("You previously covered this case while it was unsolved. It has now been "
                     "solved. Do you want to create an update video?"),
        "follow_up_job_id": fu.follow_up_job_id, "follow_up_video_id": fu.follow_up_video_id,
        "decided_at": fu.decided_at, "created_at": fu.created_at,
    }

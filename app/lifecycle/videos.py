"""Videos: what the channel has made, with YouTube metadata.

A Video row is written when a film (one language) is rendered and
updated when it is published or archived. It keeps the case's status at
production and at publication, the opening strategy, and — for an update
video — the original video it follows up. YouTube titles are rule-based
so the status is visible in the title itself:
  UNSOLVED: <title>
  SOLVED: The <case> Case — What Happened After Our Original Video
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, DocumentaryJob, FollowUpCandidate, ProductionScript, StoryVersion, Video
from app.lifecycle.status import SOLVED, UNSOLVED
from app.utils import utc_now


def _templates(language: str) -> dict[str, str]:
    t = ai_config.youtube_metadata.titles
    return t.get(language) or t["en"]


def case_name(case: Case) -> str:
    """The short name used in follow-up titles: the case title without
    leading 'The murder/disappearance of' phrases."""
    name = re.sub(r"^(the\s+)?(murder|killing|death|disappearance|case|mystery)\s+of\s+", "",
                  case.canonical_title.strip(), flags=re.I)
    return re.sub(r"\s+case$", "", name, flags=re.I).strip() or case.canonical_title


def film_title(db: Session, case: Case, ps: ProductionScript | None) -> str:
    """The film's own title: the master story's title, else the case."""
    if ps is not None:
        version = db.get(StoryVersion, ps.story_version_id)
        from app.documentary.openings import master_of

        master = master_of(db, version)
        if master is not None:
            try:
                t = (json.loads(master.narrative_structure or "{}") or {}).get("title")
            except (TypeError, ValueError):
                t = None
            if isinstance(t, str) and t.strip():
                return t.strip()
    return case.canonical_title


def youtube_title(case: Case, language: str, title: str, *, production_type: str = "original",
                  status: str | None = None) -> tuple[str, str]:
    """(title, rule). A follow-up says SOLVED/updated; an unsolved film
    says UNSOLVED — in the title itself, whatever the language."""
    tpl = _templates(language)
    status = status or case.resolution_status
    if production_type == "follow_up":
        rule, text = "follow_up", tpl["follow_up"].format(name=case_name(case), title=title)
    elif status == UNSOLVED:
        rule, text = "unsolved", tpl["unsolved"].format(title=title, name=case_name(case))
    else:
        rule, text = "original", tpl["original"].format(title=title, name=case_name(case))
    limit = ai_config.youtube_metadata.max_title_chars
    if len(text) > limit:
        # keep the status prefix, shorten the rest
        prefix, sep, rest = text.partition(": ")
        text = (prefix + sep + rest[: max(limit - len(prefix) - len(sep) - 1, 10)].rstrip() + "…"
                if sep and rule != "original" else text[: limit - 1].rstrip() + "…")
    return text, rule


def youtube_metadata(db: Session, case: Case, video: Video) -> dict:
    title, rule = youtube_title(case, video.language, video.title or case.canonical_title,
                                production_type=video.production_type,
                                status=video.status_at_publication or video.status_at_production)
    lines = []
    status = video.status_at_publication or video.status_at_production
    if video.production_type == "follow_up" and video.original_video_id:
        orig = db.get(Video, video.original_video_id)
        if orig is not None:
            ep = f"Episode {orig.episode_number}, " if orig.episode_number else ""
            when = orig.published_at.date().isoformat() if orig.published_at else "earlier"
            lines.append(f"Update to our earlier video ({ep}\"{orig.youtube_title or orig.title}\", "
                         f"{when}), made while the case was unsolved. The case has since been "
                         "solved.")
    if status == UNSOLVED:
        lines.append("Status: UNSOLVED — this case has not been solved "
                     f"(as of {utc_now().date().isoformat()}).")
    elif status == SOLVED:
        lines.append("Status: SOLVED" + (f" — {case.resolution_summary}" if case.resolution_summary
                                          else "") + ".")
    if case.location:
        lines.append(f"Location: {case.location}.")
    tags = ["true crime", "documentary", case_name(case)]
    if case.location:
        tags.append(case.location.split(",")[0].strip())
    tags.append({"UNSOLVED": "unsolved case", "SOLVED": "solved case"}.get(status or "", "case"))
    if video.production_type == "follow_up":
        tags.append("case update")
    return {"title": title, "title_rule": rule, "description": "\n".join(lines),
            "tags": [t for t in dict.fromkeys(tags) if t]}


def apply_metadata(db: Session, case: Case, video: Video) -> None:
    meta = youtube_metadata(db, case, video)
    video.youtube_title = meta["title"][:200]
    video.youtube_description = meta["description"]
    video.youtube_tags_json = json.dumps(meta["tags"], ensure_ascii=False)
    md = json.loads(video.metadata_json or "{}")
    md["title_rule"] = meta["title_rule"]
    video.metadata_json = json.dumps(md, ensure_ascii=False)


def register_render(db: Session, job: DocumentaryJob, ps: ProductionScript, info: dict) -> Video:
    """Record (or refresh) the Video of a rendered production script."""
    case = db.get(Case, ps.case_id)
    script = json.loads(ps.script_json or "{}")
    video = db.query(Video).filter(Video.production_script_id == ps.id).first()
    if video is None:
        video = Video(case_id=case.id, production_script_id=ps.id)
        db.add(video)
    video.job_id = job.id if job else None
    video.language = ps.language
    video.mode = ps.mode
    video.production_type = (job.production_type if job else None) or "original"
    video.title = film_title(db, case, ps)
    video.status_at_production = case.resolution_status or "UNKNOWN"
    video.opening_strategy = script.get("opening_strategy")
    video.file_path = info.get("path") or info.get("out") or video.file_path
    video.duration_seconds = info.get("duration") or ps.duration_seconds
    if video.production_type == "follow_up" and job and job.follow_up_id:
        fu = db.get(FollowUpCandidate, job.follow_up_id)
        if fu is not None:
            video.original_video_id = fu.original_video_id
            db.flush()
            original = db.get(Video, fu.original_video_id) if fu.original_video_id else None
            # the follow-up in the original's language is THE follow-up video
            if fu.follow_up_video_id is None or (original and video.language == original.language):
                fu.follow_up_video_id = video.id
            fu.state = "produced"
    apply_metadata(db, case, video)
    db.commit()
    db.refresh(video)
    return video


def next_episode(db: Session, language: str) -> int:
    top = db.query(func.max(Video.episode_number)).filter(Video.language == language).scalar()
    return int(top or 0) + 1


def publish(db: Session, video: Video, *, published_at: datetime | None = None,
            youtube_url: str | None = None, episode_number: int | None = None) -> Video:
    """Mark a video published: the case status at publication is frozen
    and the metadata recomputed with it."""
    case = db.get(Case, video.case_id)
    video.state = "published"
    video.published_at = published_at or utc_now()
    video.youtube_url = youtube_url or video.youtube_url
    video.episode_number = episode_number or video.episode_number or next_episode(db, video.language)
    video.status_at_publication = case.resolution_status or "UNKNOWN"
    apply_metadata(db, case, video)
    db.commit()
    db.refresh(video)
    return video


def covered_video(db: Session, case_id: int) -> Video | None:
    """The video that covered a case: the newest published original,
    else the newest rendered full film, else any video."""
    q = db.query(Video).filter(Video.case_id == case_id, Video.production_type == "original")
    for cond in (Video.state == "published", Video.mode == "full", None):
        row = (q.filter(cond) if cond is not None else q).order_by(Video.id.desc()).first()
        if row is not None:
            return row
    return None


def video_dict(v: Video) -> dict:
    return {
        "id": v.id, "case_id": v.case_id, "production_script_id": v.production_script_id,
        "job_id": v.job_id, "language": v.language, "mode": v.mode,
        "production_type": v.production_type, "original_video_id": v.original_video_id,
        "episode_number": v.episode_number, "title": v.title, "youtube_title": v.youtube_title,
        "youtube_description": v.youtube_description,
        "youtube_tags": json.loads(v.youtube_tags_json or "[]"),
        "status_at_production": v.status_at_production,
        "status_at_publication": v.status_at_publication,
        "opening_strategy": v.opening_strategy, "duration_seconds": v.duration_seconds,
        "state": v.state, "published_at": v.published_at, "youtube_url": v.youtube_url,
        "metadata": json.loads(v.metadata_json or "{}"), "created_at": v.created_at,
        "video_url": (f"/api/documentary/production/{v.production_script_id}/video.mp4"
                      if v.production_script_id else None),
    }

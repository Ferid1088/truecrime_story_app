"""Public episode identity — status labels, channel and YouTube title.

Pure rules, no network and no LLM. The status shown to the audience comes
only from the case's researched `resolution_status`; anything that is not
SOLVED or UNSOLVED has no public label and no public title (the case needs
review first).

  [Editorial Title] ([Localized Status]) | [Channel Name]
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, CaseStatusHistory, EpisodeIdentity
from app.utils import utc_now

PUBLIC_STATUS = {"SOLVED": "solved", "UNSOLVED": "unsolved"}

# "Episode 273", "Ep. 273", "#273", "Folge 273" ... never in a public title.
_EPISODE_NO = re.compile(
    r"(?:\b(?:episode|ep\.?|folge|case|fall)\s*#?\s*\d+\b|#\s*\d+\b|"
    r"(?:قسمت|پرونده|حلقة|الحلقة)\s*\d+)", re.I)


class NoPublicStatus(ValueError):
    """The case is neither SOLVED nor UNSOLVED — it cannot be titled yet."""


class TitleLocked(ValueError):
    """A published title changes only through an explicit revision."""


def public_status(status: str | None) -> str | None:
    """'solved' | 'unsolved' | None (UNKNOWN, STATUS_UNDER_REVIEW ...)."""
    return PUBLIC_STATUS.get((status or "").upper())


def channel(language: str) -> dict[str, str]:
    cfg = ai_config.video_identity.channels
    return cfg.get(language) or cfg["en"]


def status_label(status: str | None, language: str) -> str | None:
    key = public_status(status)
    if key is None:
        return None
    labels = ai_config.video_identity.status_labels
    return (labels.get(language) or labels["en"])[key]


def clean_title(title: str) -> str:
    """The editorial title without episode numbers or stray spaces."""
    return re.sub(r"\s{2,}", " ", _EPISODE_NO.sub("", title or "")).strip(" -–—:|")


def build_youtube_title(title: str, status: str | None, language: str, *,
                        episode_sequence: int | None = None) -> str:
    """The public title. Raises NoPublicStatus for a case whose status is
    not SOLVED/UNSOLVED. The editorial title is shortened (with '…') when
    needed; the status and the channel are never cut."""
    cfg = ai_config.video_identity
    label = status_label(status, language)
    if label is None:
        raise NoPublicStatus(f"status {status!r} has no public label — review the case first")
    title = clean_title(title)
    if not title:
        raise ValueError("empty editorial title")
    if cfg.include_episode_number and episode_sequence:
        title = f"{title} #{episode_sequence}"
    suffix = (cfg.channel_separator + channel(language)["name"]) if cfg.include_channel_suffix else ""

    def fmt(t: str) -> str:
        return cfg.title_format.format(title=t, status=label, channel_suffix=suffix)

    text = fmt(title)
    if len(text) > cfg.max_title_chars:
        room = cfg.max_title_chars - len(fmt("…"))
        title = title[:max(room, 1)].rstrip() + "…"
        text = fmt(title)
    return text


def resolution_provenance(db: Session, case: Case) -> dict:
    """Where the status came from: the confidence and sources of the
    latest status-history row (the naming layer reads, never sets it)."""
    row = (db.query(CaseStatusHistory).filter(CaseStatusHistory.case_id == case.id)
           .order_by(CaseStatusHistory.id.desc()).first())
    sources = json.loads(row.sources_json or "[]") if row else []
    status = case.resolution_status or "UNKNOWN"
    return {
        "case_resolution_status": status,
        "public_status": public_status(status),
        "resolution_confidence": case.resolution_confidence,
        "resolution_sources": sources,
        "changed_by": row.changed_by if row else None,
        "needs_review": public_status(status) is None,
    }


def ensure_case_uid(db: Session, case: Case) -> str:
    from app.utils import new_case_uid

    if not case.case_uid:
        taken = {u for (u,) in db.query(Case.case_uid).filter(Case.case_uid.isnot(None)).all()}
        uid = new_case_uid()
        while uid in taken:
            uid = new_case_uid()
        case.case_uid = uid
    return case.case_uid


def backfill_case_uids(db: Session) -> int:
    n = 0
    for case in db.query(Case).filter((Case.case_uid.is_(None)) | (Case.case_uid == "")).all():
        ensure_case_uid(db, case)
        n += 1
    if n:
        db.commit()
    return n


def get_identity(db: Session, case_id: int, language: str) -> EpisodeIdentity | None:
    return (db.query(EpisodeIdentity)
            .filter(EpisodeIdentity.case_id == case_id, EpisodeIdentity.language == language)
            .first())


def sync_identity(db: Session, case: Case, language: str, *, title: str | None = None,
                  episode_sequence: int | None = None, revise: bool = False,
                  status: str | None = None) -> EpisodeIdentity:
    """Create or refresh the identity of (case, language). The editorial
    title may change freely until it is published; afterwards only with
    revise=True, which bumps title_version."""
    ident = get_identity(db, case.id, language)
    if ident is None:
        ident = EpisodeIdentity(case_id=case.id, language=language,
                                channel_id=channel(language)["id"])
        db.add(ident)
    ident.case_uid = ensure_case_uid(db, case)
    ident.channel_id = channel(language)["id"]
    if episode_sequence is not None:
        ident.episode_sequence = episode_sequence
    if title is not None and title != ident.editorial_title:
        if ident.published:
            if not revise:
                raise TitleLocked("published title — create an explicit title revision")
            ident.title_version = (ident.title_version or 1) + 1
        ident.editorial_title = clean_title(title)
    if ident.published and not revise:
        # a published identity is frozen: status, label and title stay
        db.flush()
        return ident
    ident.resolution_status = status or case.resolution_status or "UNKNOWN"
    ident.resolution_label = status_label(ident.resolution_status, language)
    ident.thumbnail_status_label = ident.resolution_label
    if ident.editorial_title and ident.resolution_label:
        ident.youtube_title = build_youtube_title(
            ident.editorial_title, ident.resolution_status, language,
            episode_sequence=ident.episode_sequence)
    else:
        ident.youtube_title = None
    db.flush()
    return ident


def mark_published(db: Session, ident: EpisodeIdentity) -> EpisodeIdentity:
    if not ident.youtube_title:
        raise NoPublicStatus("no public title to publish")
    ident.published = True
    ident.published_title = ident.editorial_title
    ident.published_at = ident.published_at or utc_now()
    db.flush()
    return ident


def identity_dict(i: EpisodeIdentity) -> dict:
    return {
        "id": i.id, "case_id": i.case_id, "case_uid": i.case_uid,
        "episode_sequence": i.episode_sequence, "channel_id": i.channel_id,
        "language": i.language, "editorial_title": i.editorial_title,
        "resolution_status": i.resolution_status, "resolution_label": i.resolution_label,
        "youtube_title": i.youtube_title, "title_family_id": i.title_family_id,
        "thumbnail_title": i.thumbnail_title,
        "thumbnail_status_label": i.thumbnail_status_label,
        "host_outfit_id": i.host_outfit_id, "host_reference_asset": i.host_reference_asset,
        "published": bool(i.published), "published_title": i.published_title,
        "published_at": i.published_at, "title_version": i.title_version,
    }

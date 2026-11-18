"""The publishing findings of the Monitoring page: what needs the owner's attention among the publications. The severity of each finding is the configured one
(config/publishing.json: monitoring.events), in the page's own vocabulary (problem, attention, info)."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import (
    Case,
    FacebookUploadSession,
    Publication,
    PublishingPlanRun,
    RoutingLink,
    YouTubeUploadSession,
)
from app.publishing.config import publishing_config
from app.publishing.vocabulary import EventCategory, RoutingStatus
from app.utils import utc_now
from app.youtube_publishing.config import youtube_config

_MANUAL_METHODS = {"related_video", "link_post", "description_link", "profile_link", "comment_to_dm"}


def publishing_issues(db: Session, case_id: int | None = None, *, exclude_cases: set[int] | None = None, with_case_title: bool = False) -> list:
    """The findings of one case, or of every case (except `exclude_cases`, which another monitor already covers)."""
    from app.documentary.monitoring import Action, Issue

    cfg = publishing_config()
    area = cfg.monitoring.area
    query = db.query(Publication)
    if case_id is not None:
        query = query.filter(Publication.case_id == case_id)
    pubs = [p for p in query.order_by(Publication.id).all() if p.case_id not in (exclude_cases or set())]
    titles = {c.id: c.canonical_title for c in db.query(Case).filter(Case.id.in_({p.case_id for p in pubs})).all()} if pubs else {}
    now = utc_now()
    issues: list = []

    def raise_issue(category: EventCategory, pub: Publication, title: str, detail: str) -> None:
        prefix = f"{titles.get(pub.case_id, pub.case_id)}: " if with_case_title else ""
        issues.append(Issue(cfg.monitoring.events[category].severity.value, area, f"{prefix}{title}", detail,
                            [Action("Open Publishing", "href", f"/cases/{pub.case_id}?tab=Publishing&language={pub.language}")]))

    for pub in pubs:
        what = f"{pub.platform} {pub.kind} {pub.asset_key} ({pub.language})"
        if pub.status == "failed":
            raise_issue(EventCategory.PUBLICATION_FAILED, pub, f"Publishing failed: {what}", pub.error or "The operation failed. Review it before trying again.")
        elif pub.status == "publish_uncertain":
            raise_issue(EventCategory.PUBLICATION_UNCERTAIN, pub, f"Publish outcome unknown: {what}",
                        "The platform gave no answer: the item may be public. Check the account before any new attempt; this file is blocked from a second publish.")
        elif pub.status == "voided":
            raise_issue(EventCategory.APPROVAL_VOIDED, pub, f"Approval voided: {what}", pub.error or "Something that was approved changed. Approve it again.")
        elif pub.status == "expired":
            raise_issue(EventCategory.APPROVAL_EXPIRED, pub, f"Approval expired: {what}",
                        f"A publication approval is valid for {cfg.approval.max_age_days} days. Approve it again.")
        elif pub.status == "scheduled" and pub.window_ends_at is not None and pub.window_ends_at.replace(tzinfo=pub.window_ends_at.tzinfo or now.tzinfo) < now:
            raise_issue(EventCategory.WINDOW_MISSED, pub, f"Publication window missed: {what}",
                        f"It was due {pub.scheduled_at:%Y-%m-%d %H:%M} UTC and the {cfg.automatic_publishing.missed_window_minutes}-minute window has passed. Reschedule or publish by hand.")
        if pub.status == "published" and pub.kind == "short":
            open_links = db.query(RoutingLink).filter(RoutingLink.publication_id == pub.id, RoutingLink.method.in_(_MANUAL_METHODS),
                                                      RoutingLink.status.in_([RoutingStatus.URL_RECORDED.value, RoutingStatus.MANUAL_ACTION_REQUIRED.value])).all()
            if open_links:
                studio = f" Set it in YouTube Studio: {youtube_config().upload.studio_url.format(video_id=pub.remote_id)}" if pub.platform == "youtube" and pub.remote_id else ""
                raise_issue(EventCategory.ROUTING_ACTION_REQUIRED, pub, f"A link still needs doing: {what}",
                            "; ".join(f"{link.method} to {link.target_destination}" for link in open_links) + "." + studio)
    runs = db.query(PublishingPlanRun)
    if case_id is not None:
        runs = runs.filter(PublishingPlanRun.case_id == case_id)
    newest: dict[tuple[int, str], PublishingPlanRun] = {}
    for run in runs.order_by(PublishingPlanRun.id.desc()).all():
        if run.case_id not in (exclude_cases or set()):
            newest.setdefault((run.case_id, run.language), run)       # an older failure is history once a newer run exists: one alert per case and language
    for (run_case, language), run in newest.items():
        from app.publishing.intelligence.service import effective_status

        status = effective_status(run, cfg)
        title_prefix = f"{titles.get(run_case) or db.get(Case, run_case).canonical_title}: " if with_case_title else ""
        link = [Action("Open Publishing", "href", f"/cases/{run_case}?tab=Publishing&language={language}")]
        if status == "running":
            step = next((s for s in json.loads(run.progress_json or "[]") if s["status"] == "running"), None)
            issues.append(Issue(cfg.monitoring.events[EventCategory.PLAN_GENERATION_RUNNING].severity.value, area, f"{title_prefix}The AI publishing plan ({language}) is being made",
                                (step["label"] if step else "Starting") + ". It makes paid model calls; it stops by itself when it is done or the Emergency Stop is pressed.", link))
        elif status in ("failed", "interrupted"):
            issues.append(Issue(cfg.monitoring.events[EventCategory.PLAN_GENERATION_FAILED].severity.value, area, f"{title_prefix}The AI publishing plan ({language}) did not finish",
                                run.error or "It stopped reporting (a restart or a crash). Start it again from the Publishing tab.", link))
    sessions = db.query(YouTubeUploadSession).filter(YouTubeUploadSession.publication_id.in_([p.id for p in pubs]), YouTubeUploadSession.status.in_(["uploading", "interrupted", "uploaded", "processing"])
                                                     ).all() if pubs else []
    by_id = {p.id: p for p in pubs}
    for row in sessions:
        pub, what = by_id[row.publication_id], f"youtube {by_id[row.publication_id].kind} {by_id[row.publication_id].asset_key}"
        if row.status == "interrupted" or row.required_action:
            raise_issue(EventCategory.YOUTUBE_UPLOAD_NEEDS_ACTION, pub, f"The YouTube upload needs you: {what}", (row.error or "The upload stopped.") + f" Required action: {(row.required_action or 'resume').replace('_', ' ')}.")
        else:
            raise_issue(EventCategory.YOUTUBE_UPLOAD_RUNNING, pub, f"Uploading privately to YouTube: {what}",
                        f"{row.status}: {round(100 * row.bytes_sent / row.total_bytes) if row.total_bytes else 0}% sent" + (f", processing {row.processing_percent}%" if row.processing_percent is not None else "") + ".")
    facebook = db.query(FacebookUploadSession).filter(FacebookUploadSession.publication_id.in_([p.id for p in pubs]),
                                                      FacebookUploadSession.status.in_(["uploading", "interrupted", "processing", "ready", "drafted", "uncertain"])).all() if pubs else []
    for row in facebook:
        pub, what = by_id[row.publication_id], f"facebook {by_id[row.publication_id].kind} {by_id[row.publication_id].asset_key}"
        if row.status == "drafted":
            raise_issue(EventCategory.FACEBOOK_UPLOAD_NEEDS_ACTION, pub, f"A Facebook draft waits for you: {what}",
                        "It was saved as a draft on the Page. Look at it in Meta Business Suite; this application never publishes a draft.")
        elif row.status == "uncertain":
            raise_issue(EventCategory.FACEBOOK_UPLOAD_NEEDS_ACTION, pub, f"Facebook finish outcome unknown: {what}",
                        "Meta gave no answer to the finish call: the video may be public or a draft. Check the Page in Meta Business Suite; this file is blocked from a second upload.")
        elif row.status == "interrupted" or row.required_action:
            raise_issue(EventCategory.FACEBOOK_UPLOAD_NEEDS_ACTION, pub, f"The Facebook upload needs you: {what}", (row.error or "The upload stopped.") + f" Required action: {(row.required_action or 'resume').replace('_', ' ')}.")
        else:
            raise_issue(EventCategory.FACEBOOK_UPLOAD_RUNNING, pub, f"Uploading to Facebook (not published): {what}",
                        f"{row.status}: {round(100 * row.bytes_sent / row.total_bytes) if row.total_bytes else 0}% sent" + (f", Meta: {row.video_status}" if row.video_status else "") + ".")
    return issues


def connection_issues() -> list:
    """The findings about the Instagram account's connection (a read of the stored facts: no call to Meta). A connection that was never made is not a finding; one that broke is."""
    from app.documentary.monitoring import Action, Issue
    from app.meta_publishing.connection import InstagramConnection

    cfg = publishing_config()
    status = InstagramConnection().status()
    if not status["connected"]:
        return []
    studio = [Action("Open Publishing", "href", f"/cases/{meta_case_id()}?tab=Publishing&language={status['language']}")]
    where = f"Instagram {status['expected_account']['username']}"
    last = status["last_test"]
    if status["expired"]:
        return [Issue(cfg.monitoring.events[EventCategory.INSTAGRAM_CONNECTION_PROBLEM].severity.value, cfg.monitoring.area, f"{where}: the access token has expired",
                      "Connect Instagram again in the Publishing Studio.", studio)]
    if last is not None and not last["ok"]:
        return [Issue(cfg.monitoring.events[EventCategory.INSTAGRAM_CONNECTION_PROBLEM].severity.value, cfg.monitoring.area, f"{where}: the last connection test failed",
                      f"{last['error']} Fix it and press Test connection in the Publishing Studio.", studio)]
    if status["expiring_soon"]:
        return [Issue(cfg.monitoring.events[EventCategory.INSTAGRAM_CONNECTION_EXPIRING].severity.value, cfg.monitoring.area, f"{where}: the access token expires in {status['days_left']:g} days",
                      "Connect Instagram again before it ends.", studio)]
    return []


def facebook_connection_issues() -> list:
    """The findings about the Facebook Page's connection (a read of the stored facts: no call to Meta). A connection that was never tested is not a finding; one that broke is."""
    from app.documentary.monitoring import Action, Issue
    from app.meta_publishing.facebook_connection import FacebookConnection

    status = FacebookConnection().status()
    last = status["last_test"]
    if last is None or last["ok"]:
        return []
    cfg = publishing_config()
    return [Issue(cfg.monitoring.events[EventCategory.FACEBOOK_CONNECTION_PROBLEM].severity.value, cfg.monitoring.area, f"Facebook Page {status['page']['name']}: the last connection test failed",
                  f"{last['error']} Fix it and press Test connection in the Publishing Studio.", [Action("Open Publishing", "href", f"/cases/{meta_case_id()}?tab=Publishing&language={status['language']}")])]


def meta_case_id() -> int:
    from app.meta_publishing.config import meta_config

    return meta_config().preflight.case_id

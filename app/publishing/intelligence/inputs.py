"""What the AI publishing plan reads: the verified case dossier (facts with ids), the film's own story up to (not including) its answer, the clips' scripts, the film's description and
chapters, the capabilities of each platform, and the schedule that already exists. Everything comes from the database and the configuration; nothing is asked of a model here."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.prompts import prompt, prompt_sha256
from app.db.models import (
    Case,
    EpisodeIdentity,
    Publication,
    StoryVersion,
    VideoChapters,
    VideoDescription,
)
from app.identity.titles import public_status
from app.publishing.intelligence.examples import examples_for
from app.publishing.registry import Asset, PublicationRegistry
from app.publishing.vocabulary import Kind
from app.shortform import clips as C
from app.tags import dossier as D

# The first thing the model reads in the request: without a plain instruction a model answers a bare JSON payload as a conversation (it did, twice, in the first live run).
PLANNER_TASK = prompt("publishing/planner_task")
WRITER_TASK = prompt("publishing/metadata_task")
CRITIC_TASK = prompt("publishing/hook_critic_task")
EDITABLE_BEFORE_APPROVAL = ("draft", "voided", "expired", "failed")


@dataclass
class Inputs:
    case: Case
    language: str
    dossier: dict
    story: object | None
    cutoff: int
    clips: list[dict]
    film: dict | None
    excerpt: str
    plan_start: date
    destinations: dict[str, list[str]]
    existing: list[dict] = field(default_factory=list)
    existing_films: dict[str, list[date]] = field(default_factory=dict)
    fingerprint: str = ""

    @property
    def clip_keys(self) -> list[str]:
        return [c["clip_key"] for c in self.clips]

    @property
    def chapter_times(self) -> list[str]:
        return [c["time"] for c in (self.film or {}).get("chapters", [])]


def _story(db: Session, case: Case, version_id: int | None):
    if version_id is None:
        return None
    from app.documentary.blueprint import latest_blueprint
    from app.documentary.host import _pack
    from app.documentary.host_story import load_story

    version = db.get(StoryVersion, version_id)
    if version is None:
        return None
    blueprint = latest_blueprint(db, version.master_version_id) if version.master_version_id else None
    return load_story(db, case, version, _pack(db, case.id), blueprint)


def _excerpt(story, cutoff: int, budget: int) -> str:
    """The film's story as far as a viewer of a clip may know it: the acts before the answer, each cut to an equal share of the budget."""
    if story is None or cutoff <= 0:
        return ""
    acts = story.visible(cutoff)
    share = max(budget // max(len(acts), 1), 200)
    return "\n\n".join(a.text[:share].strip() for a in acts)[:budget]


def _film(db: Session, case: Case, language: str, asset: Asset, blockers: list[str]) -> dict:
    video = asset.video
    identity = db.query(EpisodeIdentity).filter_by(case_id=case.id, language=language).first()
    description = db.query(VideoDescription).filter_by(case_id=case.id, language=language).first()
    chapters = db.query(VideoChapters).filter_by(case_id=case.id, language=language).order_by(VideoChapters.id.desc()).first()
    items = (json.loads(chapters.chapters_json or "{}").get("chapters") if chapters is not None and chapters.status == "ok" else None) or []
    return {"video_id": video.id, "title": (identity.editorial_title if identity and identity.editorial_title else video.title) or "", "ready": not blockers, "blockers": blockers,
            "description": ((description.edited_text or description.auto_text) if description is not None else video.youtube_description or "")[:2500],
            "chapters": [{"time": c["time"], "title": c["title"]} for c in items if c.get("time") and c.get("title")]}


def _existing(db: Session, case: Case, language: str, plan_start: date, zone: ZoneInfo, horizon: int) -> tuple[list[dict], dict[str, list[date]]]:
    """What is already planned on these platforms: other cases' publications, and this case's own that the plan will not replace (approved, scheduled, in progress, published)."""
    until = plan_start + timedelta(days=horizon + 7)
    rows = db.query(Publication).filter(Publication.language == language, Publication.scheduled_at.isnot(None), Publication.status != "voided").all()
    out, films = [], {}
    for row in rows:
        if row.case_id == case.id and row.status in EDITABLE_BEFORE_APPROVAL:
            continue
        local = row.scheduled_at.astimezone(zone)
        if not plan_start - timedelta(days=7) <= local.date() <= until:
            continue
        title = json.loads(row.metadata_json or "{}").get("title") or row.caption[:40]
        out.append({"destination": f"{row.platform}/{row.kind}", "local_date": local.date().isoformat(), "local_time": local.strftime("%H:%M"), "title": title})
        if row.kind == "film":
            films.setdefault(row.platform, []).append(local.date())
    return out, films


def build(db: Session, registry: PublicationRegistry, case: Case, language: str) -> Inputs:
    cfg = registry.cfg
    assets = registry.assets(case.id, language)
    clips = []
    for asset in (a for a in assets if a.kind is Kind.SHORT):
        script = json.loads(asset.clip.script_json or "{}")
        media = (json.loads(asset.clip.media_json or "{}").get("video") or {})
        clips.append({"clip_key": asset.key, "angle": script.get("angle"), "idea": script.get("idea"), **{p: script.get(p, "") for p in C.TEXT_PARTS}, "seconds": media.get("seconds")})
    story = _story(db, case, next((a.clip.story_version_id for a in assets if a.clip is not None and a.clip.story_version_id), None))
    cutoff = C.knowledge_cutoff(story) if story is not None else 0
    film_asset = next((a for a in assets if a.kind is Kind.FILM), None)
    film = None
    if film_asset is not None:
        film_pub = db.query(Publication).filter_by(case_id=case.id, language=language, kind="film", asset_key=film_asset.key).first()
        film = _film(db, case, language, film_asset, registry.blockers(film_pub) if film_pub is not None else [])
    dossier = D.build(db, case, language)
    plan_start = registry.default_start(language)
    zone = ZoneInfo(cfg.languages[language].timezone or "UTC")
    existing, existing_films = _existing(db, case, language, plan_start, zone, cfg.intelligence.scheduling.horizon_days)
    destinations: dict[str, list[str]] = {}
    for d in cfg.destinations:
        if d.kind is Kind.FILM and film_asset is not None:
            destinations[str(d.destination)] = ["film"]
        elif d.kind is Kind.SHORT and clips:
            destinations[str(d.destination)] = [c["clip_key"] for c in clips]
    inputs = Inputs(case, language, dossier, story, cutoff, clips, film, _excerpt(story, cutoff, cfg.intelligence.generation.film_excerpt_chars), plan_start, destinations, existing, existing_films)
    inputs.fingerprint = hashlib.sha256(json.dumps({"dossier": dossier["fingerprint"], "clips": clips, "film": film and {k: v for k, v in film.items() if k not in ("ready", "blockers")},
                                                    "excerpt": inputs.excerpt, "destinations": destinations, "config": cfg.intelligence.model_dump(mode="json"),
                                                    "prompts": [prompt_sha256(n) for n in ("publishing/planner", "publishing/planner_task", "publishing/metadata", "publishing/metadata_task")]}, ensure_ascii=False, sort_keys=True,
                                                   default=str).encode()).hexdigest()
    return inputs


def routing_view(registry: PublicationRegistry, platform: str) -> list[dict]:
    """The ways to point viewers to the film from this platform, as the configuration knows them (what each really does, and how sure that is)."""
    return [{"method": r.method.value, "from": r.source, "to": r.target, "note": r.note, "evidence": r.evidence.value, "automatable": r.automatable}
            for r in registry.cfg.routing.rules if r.source.split("/")[0] == platform]


def platform_view(registry: PublicationRegistry, inputs: Inputs) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for destination in inputs.destinations:
        platform, kind = destination.split("/")
        entry = out.setdefault(platform, {"kinds": [], "restrictions": [], "routing_methods": []})
        entry["kinds"].append(kind)
        config = registry.cfg.destination(platform, kind)
        entry["restrictions"] = sorted({*entry["restrictions"], *(r.value for r in config.restrictions)})
        entry["routing_methods"] = sorted({r["method"] for r in routing_view(registry, platform)})
    return out


def planner_view(registry: PublicationRegistry, inputs: Inputs) -> dict:
    cfg, lang = registry.cfg, registry.cfg.languages[inputs.language]
    scheduling = cfg.intelligence.scheduling
    case = inputs.dossier["case"]
    return {"task": PLANNER_TASK, "language": inputs.language, "audience": {"timezone": lang.timezone, "publish_time_local": lang.publish_time_local},
            "case": {"title": case["title"], "status": case["resolution_status"], "facts": [{"id": f["id"], "claim": f["claim"]} for f in inputs.dossier["facts"][:12]]},
            "film": inputs.film and {**inputs.film, "excerpt": inputs.excerpt}, "clips": inputs.clips, "platforms": platform_view(registry, inputs),
            "constraints": {k: getattr(scheduling, k) for k in ("earliest_local_time", "latest_local_time", "max_shorts_per_platform_per_day", "min_days_between_films", "horizon_days")},
            "existing_schedule": inputs.existing, "plan_start": inputs.plan_start.isoformat()}


def analysis_for(platform: str, analysis: dict | None) -> dict | None:
    """What the metadata writer of one platform needs of the planner's judgment: each clip's strengths and risks, this platform's fit and order, its routing advice."""
    if not analysis:
        return None
    order = [e["clip_key"] for e in analysis.get("sequences", {}).get(platform, [])]
    return {"clips": [{"clip_key": c["clip_key"], "strengths": c["strengths"], "risks": c["risks"], "fit": c["platform_fit"].get(platform)} for c in analysis["clips"]],
            "order": order, "routing": analysis.get("routing", {}).get(f"{platform}/short")}


def metadata_view(registry: PublicationRegistry, inputs: Inputs, platform: str, analysis: dict | None) -> dict:
    cfg = registry.cfg
    dossier = inputs.dossier
    items = [{"kind": d.split("/")[1], "asset": a} for d, assets in inputs.destinations.items() if d.split("/")[0] == platform for a in assets]
    clips = {c["clip_key"]: c for c in inputs.clips}
    for item in items:
        if item["kind"] == "short":
            item.update({k: v for k, v in clips[item["asset"]].items() if k != "clip_key"})
    return {"task": WRITER_TASK, "language": inputs.language, "audience": {"timezone": cfg.languages[inputs.language].timezone}, "platform": platform, "channel_name": dossier["channel_name"],
            "case": {"title": dossier["case"]["title"], "status": dossier["case"]["resolution_status"], "status_public": bool(public_status(dossier["case"]["resolution_status"])),
                     "people": dossier["case"]["people"], "location": dossier["case"]["location"],
                     "facts": [{"id": f["id"], "claim": f["claim"], "disputed": f["disputed"], "uncertain": f["uncertain"]} for f in dossier["facts"][:20]]},
            "film": inputs.film and {"title": inputs.film["title"], "description": inputs.film["description"], "chapters": inputs.film["chapters"], "excerpt": inputs.excerpt},
            "items": items, "limits": {d.split("/")[1]: cfg.intelligence.metadata_limits[d].model_dump(mode="json", exclude={"note"}) for d in inputs.destinations if d.split("/")[0] == platform},
            "routing": [{"method": r["method"], "note": r["note"], "clickable": r["method"] in ("related_video", "link_post", "description_link") and r["evidence"] == "confirmed"}
                        for r in routing_view(registry, platform)],
            "analysis": analysis_for(platform, analysis), "examples": examples_for(inputs.language, cfg.intelligence.generation)}



def hook_of(item: dict, title_max: int) -> tuple[str, str]:
    """The hook of one written item and where it sits: the title where the platform has one and the writer wrote one (TikTok's "title" is the caption itself, so it may stay empty), otherwise the first line of the caption."""
    if title_max > 0 and item["title"]:
        return "title", item["title"]
    return "first_line", next((line.strip() for line in item["text"].splitlines() if line.strip()), "")


def critic_view(inputs: Inputs, platform: str, written: dict[str, dict], title_max: int, example_titles: list[str]) -> dict:
    """What the hook critic reads: each Short's hook with the clip's own words (what the hook may be built from), the case, and the language's top titles for calibration."""
    clips = {c["clip_key"]: c for c in inputs.clips}
    items = []
    for asset, item in written.items():
        source, hook = hook_of(item, title_max)
        clip = clips[asset]
        items.append({"asset": asset, "hook_source": source, "hook": hook, "hook_context": {"idea": clip.get("idea"), **{p: clip.get(p, "") for p in C.TEXT_PARTS}}})
    case = inputs.dossier["case"]
    return {"task": CRITIC_TASK, "language": inputs.language, "platform": platform,
            "case": {"title": case["title"], "status": case["resolution_status"], "status_public": bool(public_status(case["resolution_status"])), "people": case["people"], "location": case["location"],
                     "facts": [{"id": f["id"], "claim": f["claim"]} for f in inputs.dossier["facts"][:20]]},
            "examples": example_titles, "items": items}

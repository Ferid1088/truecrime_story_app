"""The case dossier the tag generator works from: the case record, its facts with ids it may cite, the language's own title and channel, and a fingerprint of all of it. The
fingerprint decides whether a tag set is stale: when a fact, the title or the language's script changes, the fingerprint changes. Nothing in it comes from the
script's wording alone."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, EpisodeIdentity, Fact, StoryVersion, VideoTagSet
from app.identity.titles import channel
from app.tags.validate import LATIN_WORD

CASE_FIELDS = ("case.title", "case.aliases", "case.people", "case.location", "case.country", "case.incident_date", "case.status")


def tagged_languages() -> list[str]:
    """Every language the case is produced in; a language added to the configuration appears here without a change in code."""
    multilingual = ai_config.multilingual
    return [*dict.fromkeys([*multilingual.production_languages, *multilingual.optional_production_languages])]


def _list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(v) for v in value if isinstance(v, (str, int))] if isinstance(value, list) else []


def _fictional(case: Case) -> bool:
    try:
        return any(isinstance(i, dict) and i.get("kind") == "fictional" for i in json.loads(case.identifiers_json or "[]"))
    except ValueError:
        return False


def _case_fields(case: Case) -> dict[str, str]:
    values = {"case.title": case.canonical_title, "case.aliases": "; ".join(_list(case.aliases_json)), "case.people": "; ".join(_list(case.people_json)),
              "case.location": case.location or "", "case.country": case.country or "", "case.incident_date": case.incident_date or "", "case.status": case.resolution_status or ""}
    return {k: v for k, v in values.items() if v}


def build(db: Session, case: Case, language: str) -> dict:
    """The dossier of one case in one language. `evidence_text` and `known_words` stay in the app (see `model_view`)."""
    cfg = ai_config.tags_settings()
    facts = (db.query(Fact).filter(Fact.case_id == case.id).order_by(Fact.confidence.desc(), Fact.id).limit(cfg.fact_limit).all())
    identity = db.query(EpisodeIdentity).filter(EpisodeIdentity.case_id == case.id, EpisodeIdentity.language == language).first()
    story = (db.query(StoryVersion).filter(StoryVersion.case_id == case.id, StoryVersion.language == language).order_by(StoryVersion.id.desc()).first())
    fields = _case_fields(case)
    evidence_text = {**fields, **{f"F{f.id}": f.claim or "" for f in facts}}
    channel_name = channel(language)["name"]
    language_title = identity.editorial_title if identity and identity.editorial_title else None
    status = case.resolution_status or "UNKNOWN"
    dossier = {
        "case_id": case.id, "language": language, "locale": cfg.locales.get(language, {}).get("audience", language),
        "limits": {"max_chars": cfg.max_chars, "target_tags": cfg.target_tags},
        "categories": [c["key"] for c in cfg.categories if c["by_model"]],
        "case": {"title": case.canonical_title, "aliases": _list(case.aliases_json), "people": _list(case.people_json), "location": case.location, "country": case.country,
                 "incident_date": case.incident_date, "resolution_status": status, "status_means": cfg.status_meaning.get(status, ""), "fictional": _fictional(case)},
        "language_title": language_title, "channel_name": channel_name,
        "facts": [{"id": f"F{f.id}", "claim": (f.claim or "")[:cfg.claim_max_chars], "disputed": bool(f.disputed), "uncertain": (f.confidence or 0) < 0.5,
                   "category": f.category, "date": f.event_date} for f in facts],
        "evidence_refs": [*fields, *(f"F{f.id}" for f in facts)],
        "evidence_text": evidence_text,
        "known_words": {w.casefold().strip("'’-") for text in (*fields.values(), language_title or "", channel_name or "") for w in LATIN_WORD.findall(text)}}
    dossier["fingerprint"] = fingerprint(dossier, story.text_hash if story else None)
    return dossier


def fingerprint(dossier: dict, script_hash: str | None) -> str:
    """The sources of a tag set: case record, facts with their text, the language's title and channel, the generator's version and the script of the language."""
    source = {"case": dossier["case"], "facts": [(f["id"], f["claim"], f["disputed"]) for f in dossier["facts"]], "title": dossier["language_title"],
              "channel": dossier["channel_name"], "version": ai_config.tags_settings().generator_version, "script": script_hash}
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def model_view(dossier: dict, english_tags: list[str] | None, examples: dict | None) -> dict:
    """What the model sees: the dossier without the app's own lookup tables, with the approved English tags and the reference examples when there are some."""
    view = {k: v for k, v in dossier.items() if k not in ("evidence_text", "known_words", "fingerprint")}
    if english_tags and dossier["language"] != "en":
        view["english_tags"] = english_tags
    if examples:
        view["reference_examples"] = examples
    return view


def approved_english(db: Session, case_id: int) -> list[str]:
    row = db.query(VideoTagSet).filter(VideoTagSet.case_id == case_id, VideoTagSet.language == "en", VideoTagSet.approval == "approved").first()
    return [t["text"] for t in json.loads(row.tags_json)] if row else []

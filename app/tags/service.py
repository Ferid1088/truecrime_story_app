"""The tag sets of a case: what the Tags tab shows, the owner's edit, approve and reject, the generation of one language or many (a language that fails does not stop the
others), and the approved tags the upload takes. A regeneration never replaces an edited or an approved list: its result waits as a candidate. Every number
shown comes from `validate`, not from the model."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import Case, VideoTagSet
from app.tags import dossier as D
from app.tags import validate as V
from app.tags.generator import TagGenerator
from app.utils import utc_now


class TagsError(RuntimeError):
    """A request the tag sets cannot take (a language the case does not have, an approval with errors, nothing to decide)."""


def _tags(raw: str | None) -> list[V.Tag]:
    return [V.Tag(t["text"], t.get("category", "manual"), t.get("evidence", [])) for t in json.loads(raw or "[]")]


def _dump(tags: list[V.Tag]) -> str:
    return json.dumps([t.as_dict() for t in tags], ensure_ascii=False)


def languages() -> list[str]:
    return D.tagged_languages()


def row_of(db: Session, case_id: int, language: str) -> VideoTagSet | None:
    return db.query(VideoTagSet).filter(VideoTagSet.case_id == case_id, VideoTagSet.language == language).first()


def ensure_language(language: str) -> None:
    if language not in languages():
        raise TagsError(f"{language} is not a language of this production")


def _status(row: VideoTagSet | None) -> str:
    """none | running | interrupted | done | failed: a set that has been running for longer than the configured time was cut off (a restart, a crash)."""
    if row is None:
        return "none"
    if row.generation_status == "running" and utc_now() - row.updated_at > timedelta(seconds=ai_config.tags_settings().running_timeout_seconds):
        return "interrupted"
    return row.generation_status


def _grouped(tags: list[V.Tag]) -> list[dict]:
    cfg = ai_config.tags_settings()
    return [{"key": c["key"], "label": c["label"], "tags": [t.text for t in tags if t.category == c["key"]]} for c in cfg.categories if any(t.category == c["key"] for t in tags)]


def view(db: Session, case: Case, language: str) -> dict:
    """One language of the Tags tab. `stale` compares the fingerprint of the facts the list was written from with the facts now."""
    row = row_of(db, case.id, language)
    dossier = D.build(db, case, language)
    tags = _tags(row.tags_json) if row else []
    report = V.check(tags, dossier, ai_config.tags_settings()) if tags else {"ok": False, "char_count": 0, "tag_count": 0, "issues": []}
    candidate = json.loads(row.candidate_json) if row and row.candidate_json else None
    return {
        "language": language, "locale": ai_config.tags_settings().locales.get(language, {}).get("label", language), "max_chars": ai_config.tags_settings().max_chars,
        "tags": [t.as_dict() for t in tags], "text": V.as_text(tags), "groups": _grouped(tags), "origin": row.origin if row else None, "approval": row.approval if row else "pending",
        "status": _status(row), "error": row.error if row else None, "stale": bool(row and tags and row.source_fingerprint != dossier["fingerprint"]),
        "generated_at": row.generated_at.isoformat() if row and row.generated_at else None, "generator_version": row.generator_version if row else None, "model": row.generation_model if row else None,
        "report": report, "fixes": json.loads(row.validation_json or "{}").get("fixes", []) if row else [], "warnings": json.loads(row.validation_json or "{}").get("warnings", []) if row else [],
        "candidate": {"tags": candidate["tags"], "text": ", ".join(t["text"] for t in candidate["tags"]), "report": candidate["report"], "generated_at": candidate["generated_at"]} if candidate else None}


def overview(db: Session, case: Case) -> dict:
    cfg = ai_config.tags_settings()
    return {"case_id": case.id, "max_chars": cfg.max_chars, "target_tags": cfg.target_tags, "categories": [{"key": c["key"], "label": c["label"]} for c in cfg.categories],
            "languages": [view(db, case, language) for language in languages()]}


def check(db: Session, case: Case, language: str, items: list[dict]) -> dict:
    """The report of a list the owner is editing (not saved), with the formatting fixes Save would make."""
    ensure_language(language)
    tags, fixes = V.repair([V.Tag(i.get("text", ""), i.get("category") or "manual", list(i.get("evidence") or [])) for i in items])
    return {**V.check(tags, D.build(db, case, language), ai_config.tags_settings()), "fixes": fixes}


def save(db: Session, case: Case, language: str, items: list[dict]) -> VideoTagSet:
    """The owner's list: formatting repaired (named in `fixes`), the origin becomes edited and an earlier approval is withdrawn. A list over the limit is saved as it is, flagged."""
    ensure_language(language)
    tags, fixes = V.repair([V.Tag(i.get("text", ""), i.get("category") or "manual", list(i.get("evidence") or [])) for i in items])
    dossier = D.build(db, case, language)
    row = row_of(db, case.id, language) or VideoTagSet(case_id=case.id, language=language, source_fingerprint=dossier["fingerprint"])
    report = V.check(tags, dossier, ai_config.tags_settings())
    validation = json.loads(row.validation_json or "{}")
    row.tags_json, row.origin, row.approval, row.approved_at = _dump(tags), "edited", "pending", None
    row.char_count = report["char_count"]
    row.validation_json = json.dumps({**validation, "fixes": fixes, "report": report}, ensure_ascii=False)
    db.add(row)
    db.commit()
    return row


def approve(db: Session, case: Case, language: str) -> VideoTagSet:
    row = row_of(db, case.id, language)
    if row is None or not _tags(row.tags_json):
        raise TagsError("There are no tags to approve")
    report = V.check(_tags(row.tags_json), D.build(db, case, language), ai_config.tags_settings())
    if not report["ok"]:
        raise TagsError("The tags have errors: " + "; ".join(i["message"] for i in report["issues"] if i["severity"] == "error")[:400])
    row.approval, row.approved_at = "approved", utc_now()
    db.commit()
    return row


def reject(db: Session, case: Case, language: str) -> VideoTagSet:
    row = row_of(db, case.id, language)
    if row is None:
        raise TagsError("There are no tags to reject")
    row.approval, row.approved_at = "rejected", None
    db.commit()
    return row


def use_candidate(db: Session, case: Case, language: str) -> VideoTagSet:
    row = row_of(db, case.id, language)
    if row is None or not row.candidate_json:
        raise TagsError("There is no new suggestion to use")
    candidate = json.loads(row.candidate_json)
    row.tags_json, row.origin, row.approval, row.approved_at = json.dumps(candidate["tags"], ensure_ascii=False), "ai", "pending", None
    row.char_count, row.source_fingerprint, row.candidate_json = candidate["report"]["char_count"], candidate["fingerprint"], None
    row.validation_json = json.dumps({"fixes": candidate["fixes"], "warnings": candidate["warnings"], "report": candidate["report"]}, ensure_ascii=False)
    row.generated_at, row.generation_model, row.generator_version = utc_now(), candidate.get("model"), ai_config.tags_settings().generator_version
    db.commit()
    return row


def drop_candidate(db: Session, case: Case, language: str) -> VideoTagSet:
    row = row_of(db, case.id, language)
    if row is None or not row.candidate_json:
        raise TagsError("There is no new suggestion to drop")
    row.candidate_json = None
    db.commit()
    return row


def _mark_running(db: Session, case_id: int, language: str) -> None:
    row = row_of(db, case_id, language) or VideoTagSet(case_id=case_id, language=language)
    row.generation_status, row.error = "running", None
    db.add(row)
    db.commit()


async def _generate_one(case_id: int, language: str) -> None:
    """One language in its own session: a failure is recorded on that language only."""
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        try:
            made = await TagGenerator().generate(db, case, language)
        except Exception as e:  # noqa: BLE001 - recorded on the language; the others go on
            db.rollback()
            row = row_of(db, case_id, language)
            row.generation_status, row.error = "failed", f"{type(e).__name__}: {e}"[:600]
            db.commit()
            return
        row = row_of(db, case_id, language)
        stamp = {"tags": [t.as_dict() for t in made["tags"]], "report": made["report"], "fixes": made["fixes"], "warnings": made["warnings"], "fingerprint": made["fingerprint"],
                 "model": made["model"], "generated_at": utc_now().isoformat()}
        if row.tags_json not in (None, "[]") and (row.origin == "edited" or row.approval == "approved"):
            row.candidate_json = json.dumps(stamp, ensure_ascii=False)
        else:
            row.tags_json, row.origin, row.approval, row.approved_at, row.candidate_json = _dump(made["tags"]), "ai", "pending", None, None
            row.char_count, row.source_fingerprint = made["report"]["char_count"], made["fingerprint"]
            row.validation_json = json.dumps({"fixes": made["fixes"], "warnings": made["warnings"], "report": made["report"]}, ensure_ascii=False)
            row.generated_at, row.generation_model, row.generator_version = utc_now(), made["model"], ai_config.tags_settings().generator_version
        row.generation_status, row.error = "done", None
        db.commit()
    finally:
        db.close()


def needs_generation(db: Session, case: Case, language: str) -> bool:
    """A language with no list, a failed or interrupted one, or one written from other facts than the present ones."""
    row = row_of(db, case.id, language)
    if row is None or not _tags(row.tags_json) and not row.candidate_json:
        return True
    return _status(row) in ("failed", "interrupted") or row.source_fingerprint != D.build(db, case, language)["fingerprint"]


async def generate(db: Session, case: Case, wanted: list[str] | None = None, *, force: bool = False) -> list[str]:
    """Generate the wanted languages (all configured ones when none are named) with at most `tags.concurrency` calls at a time. Without `force` a language that has an
    up-to-date list is left alone. Returns the languages that were generated."""
    chosen = wanted or languages()
    for language in chosen:
        ensure_language(language)
    todo = [language for language in chosen if force or needs_generation(db, case, language)]
    for language in todo:
        _mark_running(db, case.id, language)
    gate = asyncio.Semaphore(ai_config.tags_settings().concurrency)

    async def one(language: str) -> None:
        async with gate:
            await _generate_one(case.id, language)

    await asyncio.gather(*(one(language) for language in todo))
    db.expire_all()
    return todo


def approved(db: Session, case_id: int, language: str) -> list[str] | None:
    """The approved tags of this case and language in the owner's order, or None. Never the tags of another language."""
    row = row_of(db, case_id, language)
    return [t.text for t in _tags(row.tags_json)] if row and row.approval == "approved" else None


def export(db: Session, case: Case) -> dict:
    out = {}
    for language in languages():
        tags = approved(db, case.id, language)
        out[language] = {"approved": tags is not None, "tags": tags or [], "text": ", ".join(tags or [])}
    return {"case_id": case.id, "languages": out}

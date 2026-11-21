"""The owner's review of a language's titles and cover texts before any cover of that language is made (config covers.text_review_languages: Persian, whose owner reads and corrects it).
What he reviews: the film's title, the film's cover text, and for each Short its title and its cover text. He can correct each text; pressing release records a hash of exactly the texts he saw.
A text changed afterwards makes the release void, and `guard` (called when a cover is rendered) refuses until it is released again. Nothing here makes a cover, a voice or a publication."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers import compose as C
from app.covers import proposals as P
from app.db.models import Case, CoverProposal, TextApproval
from app.shortform import clips as SC


class TextsNotReleased(Exception):
    """The titles and cover texts of the language wait for the owner."""


class TextError(ValueError):
    pass


def required(language: str) -> bool:
    return language in ai_config.covers_settings().text_review_languages


def items(db: Session, case: Case, language: str) -> list[dict]:
    """Every text the owner reviews, in the order of the film: {kind, key, label, text}. `kind` is film_title, cover_text or short_title; `key` is "film", "long" or a clip key."""
    from app.identity.titles import get_identity

    ident = get_identity(db, case.id, language)
    out = [{"kind": "film_title", "key": "film", "label": "Film title", "text": (ident.editorial_title if ident is not None else "") or ""}]
    current = P.current(db, case.id, language)
    out.append({"kind": "cover_text", "key": "long", "label": "Film cover text", "text": current["long"].title if "long" in current else ""})
    clips = {row.clip_key: row for row in SC.clips_of(db, case.id, language)}
    for key in ai_config.creative_pack.clip_keys:
        script = json.loads(clips[key].script_json or "{}") if key in clips else {}
        out.append({"kind": "short_title", "key": key, "label": f"Short {key[1:]} title", "text": (script.get("package") or {}).get("title") or ""})
        out.append({"kind": "cover_text", "key": key, "label": f"Short {key[1:]} cover text", "text": current[key].title if key in current else ""})
    return out


def fingerprint(texts: list[dict]) -> str:
    return hashlib.sha256(json.dumps([[t["kind"], t["key"], t["text"]] for t in texts], ensure_ascii=False).encode("utf-8")).hexdigest()


def status(db: Session, case: Case, language: str) -> dict:
    texts = items(db, case, language)
    row = db.query(TextApproval).filter_by(case_id=case.id, language=language).first()
    valid = row is not None and row.texts_hash == fingerprint(texts)
    return {"language": language, "required": required(language), "released": valid, "changed_since_release": row is not None and not valid, "released_by": row.approved_by if row else None,
            "released_at": row.approved_at.isoformat() if row else None, "items": texts}


def guard(db: Session, case: Case, language: str) -> None:
    """Refuses unless the language needs no review or its texts are released exactly as they are now."""
    if required(language) and not status(db, case, language)["released"]:
        raise TextsNotReleased(f"The {language} titles and cover texts wait for your review: correct them if needed and release them first.")


def guard_cover(db: Session, case: Case, language: str) -> None:
    """The guard of every path that makes, previews or approves a cover: a cover error (the covers' own refusal) while the language's texts wait for the owner."""
    try:
        guard(db, case, language)
    except TextsNotReleased as exc:
        raise C.CoverError(str(exc)) from exc


def edit(db: Session, case: Case, language: str, kind: str, key: str, text: str) -> dict:
    """The owner's correction of one text. A cover text becomes a newer proposal (source owner, kept by every later run); a Short's title is written into its clip; the film's title goes through the
    film's identity (refused for a film that is already published: that needs an explicit title revision)."""
    text = " ".join(text.split())
    if not text:
        raise TextError("a text cannot be empty")
    if kind == "cover_text":
        if problems := C.title_issues(text):
            raise TextError(problems[0])
        row = P.current(db, case.id, language).get(key)
        if row is None:
            raise TextError(f"there is no {language} cover {key} to correct")
        db.add(CoverProposal(case_id=case.id, language=language, cover_key=key, kind=row.kind, version=row.version + 1, source="owner", status="open", title=text, scene_prompt=row.scene_prompt,
                             image_id=row.image_id, confidence=None, reason="corrected by the owner in the text review", validation_json=json.dumps({"issues": []})))
    elif kind == "short_title":
        clip = next((r for r in SC.clips_of(db, case.id, language) if r.clip_key == key), None)
        if clip is None:
            raise TextError(f"there is no {language} Short {key}")
        script = json.loads(clip.script_json or "{}")
        script["package"] = {**(script.get("package") or {}), "title": text}
        clip.script_json = json.dumps(script, ensure_ascii=False)
    elif kind == "film_title":
        from app.identity.titles import TitleLocked, sync_identity

        try:
            sync_identity(db, case, language, title=text)
        except TitleLocked as exc:
            raise TextError(str(exc)) from exc
    else:
        raise TextError(f"unknown text kind {kind}")
    db.commit()
    return status(db, case, language)


def release(db: Session, case: Case, language: str, approved_by: str = "owner") -> dict:
    """The owner releases the texts exactly as they are now."""
    if not required(language):
        raise TextError(f"{language} needs no text review")
    texts = items(db, case, language)
    if empty := [t["label"] for t in texts if not t["text"].strip()]:
        raise TextError(f"These texts are empty: {', '.join(empty)}")
    row = db.query(TextApproval).filter_by(case_id=case.id, language=language).first() or TextApproval(case_id=case.id, language=language, approved_by=approved_by, texts_hash="")
    row.approved_by, row.texts_hash, row.texts_json = approved_by, fingerprint(texts), json.dumps(texts, ensure_ascii=False)
    db.add(row)
    db.commit()
    return status(db, case, language)


def withdraw(db: Session, case: Case, language: str) -> dict:
    db.query(TextApproval).filter_by(case_id=case.id, language=language).delete()
    db.commit()
    return status(db, case, language)

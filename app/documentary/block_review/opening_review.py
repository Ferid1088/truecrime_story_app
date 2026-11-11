"""The hand review of the film's opening block (review kind "opening"): the same review as the host's (blocks with text and voice, corrections, takes ticked, replace
in all blocks), the blocks being the two parts of the block, the hook and the context text. A ticked take becomes the part's voice at once, its corrected words the
part's words, and the block's audio is made again from the parts (the film is built from it later). There is nothing to assemble and nothing to approve here.
Only for the languages in `voice.opening_review_languages`."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    FilmOpening,
    StoryVersion,
    VoiceBlock,
    VoiceBlockTake,
    VoiceBlockWord,
    VoiceReview,
)
from app.documentary import host_scenes as HS
from app.documentary import opening_block as OB
from app.documentary import opening_media as OM
from app.documentary import storage
from app.documentary.block_review import host_review as HR
from app.documentary.block_review import service as S
from app.documentary.voice_render import VoiceRenderer
from app.providers.voice.base import VoiceRequest

OPENING = "opening"
PARTS = ("hook", "context")


def allowed(language: str) -> bool:
    return language in ai_config.voice.opening_review_languages


def post_speed(language: str) -> float:
    return OB.post_speed_for(language)


def block_id(part: str) -> str:
    return f"opening_{part}"


def part_of(block: VoiceBlock) -> str:
    return block.block_id.removeprefix("opening_")


def row_of(db: Session, version: StoryVersion) -> FilmOpening | None:
    """The language's opening row of this version that has its voice (the block the film is built from)."""
    row = (db.query(FilmOpening).filter(FilmOpening.case_id == version.case_id, FilmOpening.language == version.language,
                                        FilmOpening.story_version_id == version.id).order_by(FilmOpening.id.desc()).first())
    return row if row is not None and (json.loads(row.media_json or "{}").get("hook")) else None


def row_of_block(db: Session, block: VoiceBlock) -> FilmOpening:
    review = db.get(VoiceReview, block.review_id)
    row = row_of(db, db.get(StoryVersion, review.story_version_id))
    if row is None:
        raise S.ReviewError("the opening block of this version is gone")
    return row


def takes_dir(db: Session, block: VoiceBlock) -> Path:
    row = row_of_block(db, block)
    return OM.opening_dir(row.case_id, row.language) / "takes"


def required(language: str, settings: dict) -> float:
    """The take must have the opening's voice values (the language's own style, less stable); returns the local speed."""
    want = OB.voice_settings_for(language)
    wrong = {k: (settings.get(k), want[k]) for k in ("stability", "speed") if settings.get(k) != want[k]}
    if wrong:
        raise S.ReviewError("the opening is not voiced: its settings are not the opening's (" + ", ".join(f"{k} {a} instead of {b}" for k, (a, b) in wrong.items()) + ")")
    return OB.post_speed_for(language)


def parts_of(db: Session, version: StoryVersion) -> list[tuple[str, dict]]:
    row = row_of(db, version)
    if row is None:
        return []
    media = json.loads(row.media_json)
    return [(part, media[part]) for part in PARTS if media.get(part)]


def original_request(row: FilmOpening, part: str, voice: dict) -> tuple[dict, Path]:
    """The take the part's voice was made from: found by the request id of the voice's record among the takes of the language's opening."""
    directory = OM.opening_dir(row.case_id, row.language) / "takes"
    fmt = ai_config.voice.elevenlabs.output_format
    for path in sorted(directory.glob(f"{block_id(part)}__*.json")):
        found = json.loads(path.read_text(encoding="utf-8"))
        if found.get("request_id") != voice.get("request_id"):
            continue
        req = VoiceRequest(text=found["text"], voice_id=found["voice_id"], model_id=found["model_id"], settings=found["settings"], seed=found["seed"],
                           previous_text=found.get("previous_text") or "", next_text=found.get("next_text") or "", language=row.language,
                           language_code=found.get("language_code"), pronunciation_dictionaries=found.get("dictionaries") or [])
        if VoiceRenderer.cache_key(req, fmt) == found["cache_key"]:
            return S.record_of(req), path
    raise S.ReviewError(f"the take of the opening's {part} is not among its files")


def stale(db: Session, version: StoryVersion, review: VoiceReview) -> bool:
    return [b.block_id for b in S.blocks_of(db, review)] != [block_id(p) for p, _ in parts_of(db, version)]


def current_review(db: Session, version: StoryVersion) -> VoiceReview | None:
    review = S.get_review(db, version, OPENING)
    return None if review is None or stale(db, version, review) else review


def discard_stale(db: Session, version: StoryVersion) -> None:
    """Removes a review of an older opening that nobody corrected; one with corrections is kept and a new review is refused until the person decides."""
    review = S.get_review(db, version, OPENING)
    if review is None or not stale(db, version, review):
        return
    blocks = S.blocks_of(db, review)
    if any(b.edited for b in blocks):
        raise S.ReviewError("the opening review in progress belongs to an older opening and has corrections: it is kept; the opening was written again")
    for b in blocks:
        db.query(VoiceBlockTake).filter_by(block_pk=b.id).delete()
        db.query(VoiceBlockWord).filter_by(block_pk=b.id).delete()
    db.query(VoiceBlock).filter_by(review_id=review.id).delete()
    db.delete(review)
    db.commit()


def open_review(db: Session, version: StoryVersion) -> VoiceReview:
    """The review of this version's opening block (made once; opening it again changes nothing). Nothing is voiced or paid."""
    if not allowed(version.language or ""):
        raise S.ReviewError(f"the opening review is not set for {version.language}")
    discard_stale(db, version)
    review = S.get_review(db, version, OPENING)
    if review is not None:
        return review
    row, parts = row_of(db, version), parts_of(db, version)
    if row is None or not parts:
        raise S.ReviewError("this version has no opening voice yet")
    found = [(part, voice, *original_request(row, part, voice)) for part, voice in parts]
    review = VoiceReview(case_id=version.case_id, story_version_id=version.id, language=version.language, kind=OPENING)
    db.add(review)
    db.flush()
    for position, (part, voice, record, _path) in enumerate(found):
        block = VoiceBlock(review_id=review.id, block_id=block_id(part), position=position, canonical_text=voice["text"], voice_text=record["text"])
        db.add(block)
        db.flush()
        db.add(VoiceBlockTake(block_pk=block.id, take_no=1, source="original", text_sent=record["text"], request_json=json.dumps(record, ensure_ascii=False),
                              duration_s=voice.get("seconds"), active=True))
    db.commit()
    return review


def apply(db: Session, block: VoiceBlock, take: VoiceBlockTake) -> None:
    """The ticked take becomes the part's voice (at the opening's local speed) and its corrected words the part's words; the block's audio is made again from
    the parts, and the film that was built from the old audio is no longer the one this block makes."""
    row, part, record = row_of_block(db, block), part_of(block), json.loads(take.request_json)
    req = S.request_of(record)
    key = VoiceRenderer.cache_key(req, ai_config.voice.elevenlabs.output_format)
    stem = takes_dir(db, block) / f"{block.block_id}__{key}"
    meta = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    required(row.language, req.settings)
    text = HR.display_text(take.text_sent)
    voice = OM.store_voice(row.case_id, row.language, part, text, stem.with_suffix(".mp3"), meta["alignment"],
                           {"text": take.text_sent, "request_id": meta.get("request_id"), "character_cost": meta.get("character_cost")})
    media = json.loads(row.media_json)
    media[part] = {**voice, "reviewed": True, "take_no": take.take_no}
    row.media_json = json.dumps(media, ensure_ascii=False)
    opening = json.loads(row.opening_json)
    opening[part == "hook" and "hook" or "context"] = text
    row.opening_json = json.dumps(opening, ensure_ascii=False)
    OM.reassemble(db, row)


def audio_path(db: Session, block: VoiceBlock, take: VoiceBlockTake) -> Path:
    """The take as it will sound in the film (at the opening's local speed), made once next to the take."""
    row = row_of_block(db, block)
    req = S.request_of(json.loads(take.request_json))
    stem = takes_dir(db, block) / f"{block.block_id}__{VoiceRenderer.cache_key(req, ai_config.voice.elevenlabs.output_format)}"
    fast = stem.with_name(stem.name + ".fast.mp3")
    if not fast.exists() and stem.with_suffix(".mp3").exists():
        audio, _ = HS.sped_up(stem.with_suffix(".mp3").read_bytes(), required(row.language, req.settings), {"starts": [], "ends": []})
        storage.write_atomic(fast, audio)
    return fast

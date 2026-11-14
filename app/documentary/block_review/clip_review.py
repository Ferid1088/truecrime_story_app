"""The hand review of the short clips of a language (review kind "clips"): the same review as the host's and the opening's, the blocks being the parts of the clips
(the hook, the context, the detail, the reaction and the invitation of each clip, in the order of the clips). A ticked take becomes the part's voice at once and its
corrected words the part's words. There is nothing to assemble and nothing to approve here. Only for the languages in `voice.clip_review_languages`."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    ShortClip,
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
from app.shortform import clip_media as CM
from app.shortform import clips as C

CLIPS = "clips"


def allowed(language: str) -> bool:
    return language in ai_config.voice.clip_review_languages


def post_speed(language: str) -> float:
    return OB.post_speed_for(language)


def split_block(block: VoiceBlock) -> tuple[str, str]:
    """The clip key and the part a block belongs to (`clip_C1_hook`)."""
    _, key, part = block.block_id.split("_", 2)
    return key, part


def rows_of(db: Session, version: StoryVersion) -> list[ShortClip]:
    """The newest valid version of each clip of this version's language that has its voices, in the order of the clips."""
    rows = [r for r in C.clips_of(db, version.case_id, version.language or "") if r.story_version_id == version.id and json.loads(r.media_json or "{}").get("voice")]
    return [r for r in rows if not OM.stale_voices({p: json.loads(r.script_json)[p] for p in CM.PARTS}, json.loads(r.media_json)["voice"])]


def parts_of(db: Session, version: StoryVersion) -> list[tuple[str, dict, str]]:
    """(block id, the part's voice record, the clip key) of every part, clip by clip."""
    out = []
    for row in rows_of(db, version):
        voice = json.loads(row.media_json)["voice"]
        out += [(CM.block_id(row.clip_key, part), voice[part], row.clip_key) for part in CM.PARTS if voice.get(part)]
    return out


def row_of_block(db: Session, block: VoiceBlock) -> ShortClip:
    review = db.get(VoiceReview, block.review_id)
    key, _ = split_block(block)
    row = next((r for r in rows_of(db, db.get(StoryVersion, review.story_version_id)) if r.clip_key == key), None)
    if row is None:
        raise S.ReviewError(f"the clip {key} of this version is gone")
    return row


def takes_dir(db: Session, block: VoiceBlock) -> Path:
    row = row_of_block(db, block)
    return CM.clip_dir(row.case_id, row.language) / "takes"


def required(language: str, settings: dict) -> float:
    """The take must have the clips' voice values (the opening hook's: the language's own style, less stable); returns the local speed."""
    want = OB.voice_settings_for(language)
    wrong = {k: (settings.get(k), want[k]) for k in ("stability", "speed") if settings.get(k) != want[k]}
    if wrong:
        raise S.ReviewError("the clip is not voiced: its settings are not the clips' (" + ", ".join(f"{k} {a} instead of {b}" for k, (a, b) in wrong.items()) + ")")
    return OB.post_speed_for(language)


def original_request(row: ShortClip, block_id: str, voice: dict) -> tuple[dict, Path]:
    """The take the part's voice was made from: found by the request id of the voice's record among the takes of the clip's block."""
    directory = CM.clip_dir(row.case_id, row.language) / "takes"
    fmt = ai_config.voice.elevenlabs.output_format
    for path in sorted(directory.glob(f"{block_id}__*.json")):
        found = json.loads(path.read_text(encoding="utf-8"))
        if found.get("request_id") != voice.get("request_id"):
            continue
        req = VoiceRequest(text=found["text"], voice_id=found["voice_id"], model_id=found["model_id"], settings=found["settings"], seed=found["seed"],
                           previous_text=found.get("previous_text") or "", next_text=found.get("next_text") or "", language=row.language,
                           language_code=found.get("language_code"), pronunciation_dictionaries=found.get("dictionaries") or [])
        if VoiceRenderer.cache_key(req, fmt) == found["cache_key"]:
            return S.record_of(req), path
    raise S.ReviewError(f"the take of {block_id} is not among its files")


def stale(db: Session, version: StoryVersion, review: VoiceReview) -> bool:
    return [b.block_id for b in S.blocks_of(db, review)] != [bid for bid, _, _ in parts_of(db, version)]


def current_review(db: Session, version: StoryVersion) -> VoiceReview | None:
    review = S.get_review(db, version, CLIPS)
    return None if review is None or stale(db, version, review) else review


def discard_stale(db: Session, version: StoryVersion) -> None:
    """Removes a review of older clips that nobody corrected; one with corrections is kept and a new review is refused until the person decides."""
    review = S.get_review(db, version, CLIPS)
    if review is None or not stale(db, version, review):
        return
    blocks = S.blocks_of(db, review)
    if any(b.edited for b in blocks):
        raise S.ReviewError("the clip review in progress belongs to older clips and has corrections: it is kept; the clips were written again")
    for b in blocks:
        db.query(VoiceBlockTake).filter_by(block_pk=b.id).delete()
        db.query(VoiceBlockWord).filter_by(block_pk=b.id).delete()
    db.query(VoiceBlock).filter_by(review_id=review.id).delete()
    db.delete(review)
    db.commit()


def open_review(db: Session, version: StoryVersion) -> VoiceReview:
    """The review of this version's clips (made once; opening it again changes nothing). Nothing is voiced or paid."""
    if not allowed(version.language or ""):
        raise S.ReviewError(f"the clip review is not set for {version.language}")
    discard_stale(db, version)
    review = S.get_review(db, version, CLIPS)
    if review is not None:
        return review
    parts = parts_of(db, version)
    if not parts:
        raise S.ReviewError("this version has no clip voices yet")
    rows = {r.clip_key: r for r in rows_of(db, version)}
    found = [(bid, voice, *original_request(rows[key], bid, voice)) for bid, voice, key in parts]
    review = VoiceReview(case_id=version.case_id, story_version_id=version.id, language=version.language, kind=CLIPS)
    db.add(review)
    db.flush()
    for position, (bid, voice, record, _path) in enumerate(found):
        block = VoiceBlock(review_id=review.id, block_id=bid, position=position, canonical_text=voice["text"], voice_text=record["text"])
        db.add(block)
        db.flush()
        db.add(VoiceBlockTake(block_pk=block.id, take_no=1, source="original", text_sent=record["text"], request_json=json.dumps(record, ensure_ascii=False),
                              duration_s=voice.get("seconds"), active=True))
    db.commit()
    return review


def apply(db: Session, block: VoiceBlock, take: VoiceBlockTake) -> None:
    """The ticked take becomes the part's voice (at the clips' local speed) and its corrected words the part's words in the clip's script."""
    row, record = row_of_block(db, block), json.loads(take.request_json)
    _, part = split_block(block)
    req = S.request_of(record)
    key = VoiceRenderer.cache_key(req, ai_config.voice.elevenlabs.output_format)
    stem = takes_dir(db, block) / f"{block.block_id}__{key}"
    meta = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    required(row.language, req.settings)
    text = HR.display_text(take.text_sent)
    voice = OM.store_voice(row.case_id, row.language, part, text, stem.with_suffix(".mp3"), meta["alignment"],
                           {"text": take.text_sent, "request_id": meta.get("request_id"), "character_cost": meta.get("character_cost")},
                           folder=CM.clip_dir(row.case_id, row.language), stem=block.block_id, tempo=OB.post_speed_for(row.language))
    media = json.loads(row.media_json)
    media["voice"][part] = {**voice, "reviewed": True, "take_no": take.take_no}
    media["voice_seconds"] = round(sum(v["seconds"] for v in media["voice"].values()), 3)
    row.media_json = json.dumps(media, ensure_ascii=False)
    script = json.loads(row.script_json)
    script[part] = text
    row.script_json = json.dumps(script, ensure_ascii=False)


def audio_path(db: Session, block: VoiceBlock, take: VoiceBlockTake) -> Path:
    """The take as it will sound in the clip (at the clips' local speed), made once next to the take."""
    row = row_of_block(db, block)
    req = S.request_of(json.loads(take.request_json))
    stem = takes_dir(db, block) / f"{block.block_id}__{VoiceRenderer.cache_key(req, ai_config.voice.elevenlabs.output_format)}"
    fast = stem.with_name(stem.name + ".fast.mp3")
    if not fast.exists() and stem.with_suffix(".mp3").exists():
        audio, _ = HS.sped_up(stem.with_suffix(".mp3").read_bytes(), required(row.language, req.settings), {"starts": [], "ends": []})
        storage.write_atomic(fast, audio)
    return fast

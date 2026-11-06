"""The hand review of the host's voice (review kind "host"): the same review as the narration's (blocks with text and voice, corrections, words and their alias,
takes ticked, replace in all blocks), the blocks being the host's scenes of a version. A ticked take becomes the scene's voice at once (the avatar is made
again from it, never from an older voice). There is nothing to assemble and nothing to approve here: the approval of a film's voice is the narration review's
alone. Only for the languages in `voice.host_review_languages`."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import HostScene, StoryVersion, VoiceBlock, VoiceBlockTake, VoiceBlockWord, VoiceReview
from app.documentary import host_scenes as HS
from app.documentary.host import latest_host_segments
from app.documentary.block_review import service as S
from app.documentary.reading.words import VOWEL_MARKS
from app.documentary.voice_render import VoiceRenderer
from app.providers.voice.base import VoiceRequest

HOST = "host"


def allowed(language: str) -> bool:
    return language in ai_config.voice.host_review_languages


def scene_of(db: Session, block: VoiceBlock) -> HostScene:
    scene = db.get(HostScene, int(block.block_id.removeprefix("host")))
    if scene is None:
        raise S.ReviewError(f"the host scene of block {block.block_id} is gone")
    return scene


def takes_dir(db: Session, block: VoiceBlock) -> Path:
    return HS.scene_dir(scene_of(db, block)) / "takes"


def required(language: str, settings: dict) -> float:
    """The host's take must have the story voice's values (the same check the host's voice step makes); returns the post speed."""
    try:
        return HS.required_voice(language, settings)
    except HS.ST.StudioError as e:
        raise S.ReviewError(str(e)) from e


def scenes_of(db: Session, version: StoryVersion) -> list[HostScene]:
    """The host's scenes of the version's newest host text that have a voice, in the order of the film (the scenes of an earlier text are not reviewed)."""
    segments = latest_host_segments(db, version.id)
    if segments is None:
        return []
    rows = db.query(HostScene).filter(HostScene.host_segments_id == segments.id, HostScene.language == version.language, HostScene.voice_path.isnot(None)).all()
    return sorted(rows, key=lambda s: (int(re.sub(r"\D", "", s.host_segment_id) or 0), s.id))


def original_request(scene: HostScene) -> tuple[dict, Path]:
    """The take the scene's voice was made from: its request, found by the text and the request id of the voice's sidecar among the takes of the scene."""
    meta, directory = json.loads(scene.voice_meta_json or "{}"), HS.scene_dir(scene) / "takes"
    sidecar = HS.storage.resolve(scene.voice_path).with_name(HS.storage.resolve(scene.voice_path).name + ".json")
    tts_text = json.loads(sidecar.read_text(encoding="utf-8")).get("tts_text") if sidecar.exists() else None
    fmt = ai_config.voice.elevenlabs.output_format
    for path in sorted(directory.glob(f"host{scene.id}__*.json")):
        found = json.loads(path.read_text(encoding="utf-8"))
        if found.get("request_id") != meta.get("request_id") or (tts_text and found.get("text") != tts_text):
            continue
        req = VoiceRequest(text=found["text"], voice_id=found["voice_id"], model_id=found["model_id"], settings=found["settings"], seed=found["seed"],
                           previous_text=found.get("previous_text") or "", next_text=found.get("next_text") or "", language=scene.language,
                           language_code=found.get("language_code"), pronunciation_dictionaries=found.get("dictionaries") or [])
        if VoiceRenderer.cache_key(req, fmt) == found["cache_key"]:
            return S.record_of(req), path
    raise S.ReviewError(f"the take of the host scene {scene.id} is not among its files")


def stale(db: Session, version: StoryVersion, review: VoiceReview) -> bool:
    """A review of scenes that are no longer the version's newest host text (the host's words were written again)."""
    return [b.block_id for b in S.blocks_of(db, review)] != [f"host{s.id}" for s in scenes_of(db, version)]


def current_review(db: Session, version: StoryVersion) -> VoiceReview | None:
    """The version's host voice review, unless it belongs to an older host text."""
    review = S.get_review(db, version, HOST)
    return None if review is None or stale(db, version, review) else review


def discard_stale(db: Session, version: StoryVersion) -> None:
    """Removes a review of an older host text that nobody corrected (nothing is lost); one with corrections is kept and the new review is refused until
    the person decides."""
    review = S.get_review(db, version, HOST)
    if review is None or not stale(db, version, review):
        return
    blocks = S.blocks_of(db, review)
    if any(b.edited for b in blocks):
        raise S.ReviewError("the host voice review in progress belongs to an older host text and has corrections: it is kept; the host's words were written again")
    for b in blocks:
        db.query(VoiceBlockTake).filter_by(block_pk=b.id).delete()
        db.query(VoiceBlockWord).filter_by(block_pk=b.id).delete()
    db.query(VoiceBlock).filter_by(review_id=review.id).delete()
    db.delete(review)
    db.commit()


def open_review(db: Session, version: StoryVersion) -> VoiceReview:
    """The review of the host's voice of this version's newest host text (made once; opening it again changes nothing). Nothing is voiced or paid."""
    if not allowed(version.language or ""):
        raise S.ReviewError(f"the host voice review is not set for {version.language}")
    discard_stale(db, version)
    review = S.get_review(db, version, HOST)
    if review is not None:
        return review
    scenes = scenes_of(db, version)
    if not scenes:
        raise S.ReviewError("this version has no host voice yet")
    found = [(scene, *original_request(scene)) for scene in scenes]
    review = VoiceReview(case_id=version.case_id, story_version_id=version.id, language=version.language, kind=HOST)
    db.add(review)
    db.flush()
    for position, (scene, record, _path) in enumerate(found):
        block = VoiceBlock(review_id=review.id, block_id=f"host{scene.id}", position=position, canonical_text=scene.text, voice_text=record["text"])
        db.add(block)
        db.flush()
        db.add(VoiceBlockTake(block_pk=block.id, take_no=1, source="original", text_sent=record["text"], request_json=json.dumps(record, ensure_ascii=False),
                              duration_s=scene.voice_seconds, active=True))
    db.commit()
    return review


def display_text(voiced: str) -> str:
    """The words of a voiced text as the Host tab and the subtitles show them: the text the person corrected, without the vowel marks the reading stage and the
    person put in for the voice."""
    return VOWEL_MARKS.sub("", voiced)


def sync_text(db: Session, scene: HostScene, take: VoiceBlockTake) -> bool:
    """The scene's words become the corrected words of the ticked take (the Host tab shows the version after the person's correction, not the one the writer
    wrote). The voice audio is the same, so its record follows the new text and the avatar already made stays valid; a direction made from the old words is
    asked for again as long as no avatar has been requested. Returns whether the text changed."""
    text = display_text(take.text_sent)
    if text == scene.text:
        return False
    old_sha, scene.text, scene.text_sha256 = scene.text_sha256, text, HS._sha(text)
    meta = json.loads(scene.voice_meta_json or "{}")
    if meta.get("text_sha256") == old_sha:
        scene.voice_meta_json = json.dumps({**meta, "text_sha256": scene.text_sha256})
    row = db.get(HS.HostSegments, scene.host_segments_id)
    segments = json.loads(row.segments_json or "[]")
    for seg in segments:
        if seg.get("segment_id") == scene.host_segment_id:
            seg["avatar_dialogue"] = text
    row.segments_json = json.dumps(segments, ensure_ascii=False)
    if not scene.provider_job_id and not scene.avatar_video_path:
        scene.direction_json = scene.direction_sha256 = None
    return True


def apply(db: Session, block: VoiceBlock, take: VoiceBlockTake) -> None:
    """The ticked take becomes the scene's voice (at the language's post speed) and its corrected words the scene's words, and everything made from the old
    voice is invalid."""
    scene, record = scene_of(db, block), json.loads(take.request_json)
    sync_text(db, scene, take)
    req = S.request_of(record)
    key = VoiceRenderer.cache_key(req, ai_config.voice.elevenlabs.output_format)
    stem = takes_dir(db, block) / f"{block.block_id}__{key}"
    meta = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    tempo = required(scene.language, req.settings)
    HS.store_voice(scene, {"raw_path": str(stem.with_suffix(".mp3")), "alignment": meta["alignment"], "cache_key": key, "text": take.text_sent,
                           "request_id": meta.get("request_id"), "character_cost": meta.get("character_cost")},
                   req.settings, tempo, req.pronunciation_dictionaries, reviewed=True)
    checks = json.loads(scene.voice_checks_json or "{}")
    checks.update({"tts_text": take.text_sent, "reviewed_take": take.take_no})
    scene.voice_checks_json = json.dumps(checks, ensure_ascii=False)


def audio_path(db: Session, block: VoiceBlock, take: VoiceBlockTake) -> Path:
    """The take as it will sound in the film (at the post speed), made once next to the take."""
    req = S.request_of(json.loads(take.request_json))
    stem = takes_dir(db, block) / f"{block.block_id}__{VoiceRenderer.cache_key(req, ai_config.voice.elevenlabs.output_format)}"
    fast = stem.with_name(stem.name + ".fast.mp3")
    if not fast.exists() and stem.with_suffix(".mp3").exists():
        audio, _ = HS.sped_up(stem.with_suffix(".mp3").read_bytes(), required(block_language(db, block), req.settings), {"starts": [], "ends": []})
        HS._write_atomic(fast, audio)
    return fast


def block_language(db: Session, block: VoiceBlock) -> str:
    return scene_of(db, block).language

"""Host scenes: a written host segment → its voice → its avatar video,
one saved step at a time.

Every step stores its result (row fields, files with sha256, a history
entry) before the next one starts. Running a scene again continues from
the first step whose result is missing or no longer matches its input:

  planned          text frozen (sha256), studio + framing resolved
  voice_ready      ElevenLabs audio of exactly that text, saved with a
                   sidecar (text sha, voice, model, request id, timing)
  avatar_uploaded  the audio uploaded to the avatar provider (asset id)
  avatar_requested the provider job accepted (job id) — a retried request
                   carries the same Idempotency-Key, so it is never paid twice
  avatar_ready     the video downloaded (atomic), sha256 stored

A failure records failed_step + last_error and leaves the status at the
last good step. A provider job that FAILED moves to a new generation (a
new Idempotency-Key) on the next run; a job that is merely slow is polled
again, never re-requested.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import HostScene, HostSegments, StoryVersion
from app.documentary import storage
from app.documentary import studio as ST
from app.utils import utc_now

log = logging.getLogger(__name__)

STEPS = ("plan", "voice", "avatar_upload", "avatar_request", "avatar_download")
STATUS_AFTER = {"plan": "planned", "voice": "voice_ready", "avatar_upload": "avatar_uploaded",
                "avatar_request": "avatar_requested", "avatar_download": "avatar_ready"}
ORDER = ["planned", "voice_ready", "avatar_uploaded", "avatar_requested", "avatar_ready",
         "composited"]


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _loads(text: str | None, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def scene_dir(scene: HostScene) -> Path:
    d = storage.case_dir(scene.case_id) / "host" / scene.language / f"scene_{scene.id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log(scene: HostScene, step: str, outcome: str, detail: dict | str | None = None) -> None:
    hist = _loads(scene.history_json, [])
    hist.append({"at": utc_now().isoformat(), "step": step, "outcome": outcome,
                 **({"detail": detail} if detail else {})})
    scene.history_json = json.dumps(hist[-200:], ensure_ascii=False)
    if outcome in ("ok", "failed"):
        att = _loads(scene.attempts_json, {})
        att[step] = att.get(step, 0) + 1
        scene.attempts_json = json.dumps(att)
    scene.updated_at = utc_now()


def _done(scene: HostScene, step: str, db: Session, result: dict | None = None,
          detail: dict | None = None) -> None:
    new = STATUS_AFTER[step]
    if ORDER.index(new) > ORDER.index(scene.status if scene.status in ORDER else "planned"):
        scene.status = new
    scene.failed_step = None
    scene.last_error = None
    if result and result.get("skipped"):
        _log(scene, step, "skipped", result["skipped"])
    else:
        _log(scene, step, "ok", detail)
    db.commit()


def _fail(scene: HostScene, step: str, err: Exception, db: Session) -> None:
    scene.failed_step = step
    scene.last_error = f"{type(err).__name__}: {err}"[:1000]
    _log(scene, step, "failed", {"error": scene.last_error,
                                 "kind": getattr(err, "kind", None)})
    db.commit()


# ---------------------------------------------------------------------------
# planning (no provider calls)
# ---------------------------------------------------------------------------


def plan_host_scenes(db: Session, seg_row: HostSegments) -> list[HostScene]:
    """One scene per written segment the host critic approved, with the
    channel's own studio and the framing for its position. Idempotent: a
    scene that exists for this segments row is kept (its text is frozen);
    nothing is planned for a segment without dialogue or one that did not
    pass its check."""
    version = db.get(StoryVersion, seg_row.story_version_id)
    language = seg_row.language or (version.language if version else "en")
    cp = ST.channel_profile(language)
    r = ST.resolve(language)
    segs = _loads(seg_row.segments_json, [])
    existing = {s.host_segment_id: s for s in db.query(HostScene).filter(
        HostScene.host_segments_id == seg_row.id).all()}
    out = []
    for seg in segs:
        sid = seg.get("segment_id")
        text = (seg.get("avatar_dialogue") or "").strip()
        if not sid or not text:
            continue
        if (seg.get("quality") or {}).get("pass") is False:
            continue  # not approved by the host critic: no scene (left out)
        if sid in existing:
            out.append(existing[sid])
            continue
        preset_name = ai_config.studio.framing_by_position.get(seg.get("position") or "",
                                                               "HOST_MEDIUM")
        if preset_name not in r["presets"]:
            preset_name = "HOST_MEDIUM"
        preset, asset = r["presets"][preset_name]
        scene = HostScene(
            case_id=seg_row.case_id, story_version_id=seg_row.story_version_id,
            host_segments_id=seg_row.id, host_segment_id=sid, language=language,
            channel=cp["channel_name"], position=seg.get("position"), beat_id=seg.get("beat_id"),
            text=text, text_sha256=_sha(text),
            studio_profile_id=cp["studio_profile_id"], studio_asset_id=asset.id,
            framing_preset=preset_name,
            host_position_json=json.dumps({"center_x": preset.host_center_x,
                                           "bottom": preset.host_bottom}),
            host_scale=preset.host_height_ratio,
            background_mode=r["profile"].background_mode,
            planned_duration=seg.get("estimated_seconds"),
            voice_id=cp["elevenlabs_voice_id"], avatar_provider=ai_config.avatar.provider,
            status="planned")
        _log(scene, "plan", "ok", {"studio": asset.id, "preset": preset_name,
                                   "text_sha256": scene.text_sha256[:16]})
        db.add(scene)
        out.append(scene)
    db.commit()
    return out


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


async def _step_voice(db: Session, scene: HostScene, provider=None) -> dict | None:
    """ElevenLabs audio of exactly the frozen text (reused when the saved
    file still matches text, voice and model)."""
    from app.providers.voice import get_voice_provider
    from app.providers.voice.base import VoiceRequest

    vcfg = ai_config.voice.languages.get(scene.language)
    if vcfg is None or not scene.voice_id:
        raise ST.StudioError(f"no voice configured for {scene.language}")
    meta = _loads(scene.voice_meta_json, {})
    p = storage.resolve(scene.voice_path) if scene.voice_path else None
    if (p is not None and p.exists() and meta.get("text_sha256") == scene.text_sha256
            and meta.get("voice_id") == scene.voice_id and meta.get("model_id") == vcfg.model_id
            and _sha(p.read_bytes()) == scene.voice_sha256):
        return {"skipped": "saved audio matches the text"}
    settings = ai_config.voice.styles[ai_config.voice.default_style].model_dump()
    req = VoiceRequest(text=scene.text, voice_id=scene.voice_id, model_id=vcfg.model_id,
                       settings=settings, language=scene.language,
                       language_code=vcfg.language_code)
    res = await (provider or get_voice_provider()).synthesize(req)
    d = scene_dir(scene)
    name = f"voice_{scene.text_sha256[:12]}_{scene.voice_id[:8]}.mp3"
    _write_atomic(d / name, res.audio)
    seconds = round(max(res.char_ends), 3) if res.char_ends else None
    meta = {"text_sha256": scene.text_sha256, "voice_id": scene.voice_id,
            "model_id": vcfg.model_id, "settings": settings,
            "request_id": res.request_id, "character_cost": res.character_cost,
            "audio_format": res.audio_format, "seconds": seconds,
            "created_at": utc_now().isoformat()}
    _write_atomic(d / (name + ".json"), json.dumps(
        {**meta, "text": scene.text, "alignment": {"characters": res.characters,
                                                   "starts": res.char_starts,
                                                   "ends": res.char_ends}},
        ensure_ascii=False).encode("utf-8"))
    scene.voice_path = storage.rel(d / name)
    scene.voice_sha256 = _sha(res.audio)
    scene.voice_seconds = seconds
    scene.voice_meta_json = json.dumps(meta)
    # a new voice invalidates everything the provider made from the old one
    scene.provider_asset_id = None
    scene.provider_job_id = None
    scene.avatar_video_path = None
    scene.avatar_video_sha256 = None
    if ORDER.index(scene.status) > ORDER.index("voice_ready"):
        scene.status = "voice_ready"


async def _step_avatar_upload(db: Session, scene: HostScene, prov) -> dict | None:
    if scene.provider_asset_id and scene.provider_asset_voice_sha == scene.voice_sha256:
        return {"skipped": "audio already uploaded"}
    p = storage.resolve(scene.voice_path)
    if p is None or not p.exists() or _sha(p.read_bytes()) != scene.voice_sha256:
        raise ST.StudioError("the saved voice file is missing or changed — run the voice step")
    scene.provider_asset_id = await prov.upload_asset(p, "audio/mpeg")
    scene.provider_asset_voice_sha = scene.voice_sha256
    # the background image for the provider-composited fallback
    if scene.background_mode == "provider_composited":
        meta = _loads(scene.avatar_meta_json, {})
        reg = ST.load_registry()
        a = reg.asset(scene.studio_asset_id)
        if a is None or a.language != scene.language:
            raise ST.StudioError(f"studio {scene.studio_asset_id} is not a {scene.language} asset")
        if meta.get("background_asset_sha") != a.sha256:
            meta["background_asset_id"] = await prov.upload_asset(ST.asset_path(reg, a),
                                                                  "image/png")
            meta["background_asset_sha"] = a.sha256
        scene.avatar_meta_json = json.dumps(meta)


async def _step_avatar_request(db: Session, scene: HostScene, prov, look_id: str) -> dict | None:
    if (scene.provider_job_id and scene.provider_job_voice_sha == scene.voice_sha256):
        return {"skipped": f"job {scene.provider_job_id} exists"}
    meta = _loads(scene.avatar_meta_json, {})
    composited = scene.background_mode == "provider_composited"
    key = f"tc-host-{scene.id}-{(scene.voice_sha256 or '')[:12]}-g{scene.provider_generation}"
    job = await prov.create_video(
        look_id, scene.provider_asset_id, key,
        output_format="mp4" if composited else ai_config.avatar.output_format,
        resolution=ai_config.avatar.resolution,
        background_asset_id=meta.get("background_asset_id") if composited else None,
        title=f"{scene.channel} case {scene.case_id} {scene.host_segment_id}")
    scene.avatar_id = look_id
    scene.provider_job_id = job.job_id
    scene.provider_job_voice_sha = scene.voice_sha256
    meta.update({"idempotency_key": key, "requested_at": utc_now().isoformat(),
                 "output_format": "mp4" if composited else ai_config.avatar.output_format})
    scene.avatar_meta_json = json.dumps(meta)


async def _step_avatar_download(db: Session, scene: HostScene, prov,
                                sleep=asyncio.sleep) -> dict | None:
    from app.providers.avatar.base import AvatarProviderError

    p = storage.resolve(scene.avatar_video_path) if scene.avatar_video_path else None
    if p is not None and p.exists() and _sha(p.read_bytes()) == scene.avatar_video_sha256:
        return {"skipped": "video already saved"}
    deadline = utc_now() + timedelta(minutes=ai_config.avatar.max_poll_minutes)
    while True:
        job = await prov.get_video(scene.provider_job_id)
        if job.status == "completed" and job.video_url:
            break
        if job.status == "failed":
            # the provider gave up on this job: the next run requests anew
            scene.provider_generation = (scene.provider_generation or 0) + 1
            scene.provider_job_id = None
            scene.provider_job_voice_sha = None
            scene.status = "avatar_uploaded"
            raise AvatarProviderError("job_failed", job.failure_message or "avatar job failed")
        if utc_now() >= deadline:
            raise AvatarProviderError("timeout", f"job {job.job_id} still {job.status} — "
                                                 "run again to keep waiting")
        await sleep(ai_config.avatar.poll_interval_s)
    meta = _loads(scene.avatar_meta_json, {})
    ext = "mp4" if meta.get("output_format") == "mp4" else "webm"
    dest = scene_dir(scene) / f"avatar_{scene.provider_job_id}.{ext}"
    await prov.download(job.video_url, dest)
    scene.avatar_video_path = storage.rel(dest)
    scene.avatar_video_sha256 = _sha(dest.read_bytes())
    meta.update({"duration": job.duration, "downloaded_at": utc_now().isoformat(),
                 **job.extra})
    scene.avatar_meta_json = json.dumps(meta)


async def run_scene(db: Session, scene: HostScene, until: str = "voice",
                    voice_provider=None, avatar_provider=None, sleep=asyncio.sleep) -> HostScene:
    """Run the scene's steps up to `until` ("voice" or "avatar"), starting
    from the first one whose saved result is missing. Each step commits its
    result before the next begins; a failure is recorded and re-raised."""
    if until not in ("voice", "avatar"):
        raise ValueError("until must be 'voice' or 'avatar'")
    scene.running_since = utc_now()
    db.commit()
    step = "voice"
    try:
        r = await _step_voice(db, scene, voice_provider)
        _done(scene, "voice", db, r, {"voice_sha256": (scene.voice_sha256 or "")[:16],
                                      "seconds": scene.voice_seconds})
        if until == "voice":
            return scene
        step = "avatar_upload"
        if not ai_config.avatar.enabled and avatar_provider is None:
            raise ST.StudioError("avatar generation is disabled (config avatar.enabled)")
        from app.providers.avatar import get_avatar_provider

        cp = ST.channel_profile(scene.language)
        prov = avatar_provider or get_avatar_provider(key_env=cp["heygen_key_env"])
        step = "avatar_upload"
        r = await _step_avatar_upload(db, scene, prov)
        _done(scene, "avatar_upload", db, r, {"asset_id": scene.provider_asset_id})
        step = "avatar_request"
        look = scene.avatar_id
        if not look:
            avatar_env = cp["heygen_avatar_env"]
            configured = os.getenv(avatar_env) or ""
            if not configured:
                raise ST.StudioError(f"{avatar_env} is not set (.env)")
            look = await prov.resolve_look(configured)
        r = await _step_avatar_request(db, scene, prov, look)
        _done(scene, "avatar_request", db, r, {"job_id": scene.provider_job_id,
                                               "generation": scene.provider_generation})
        step = "avatar_download"
        r = await _step_avatar_download(db, scene, prov, sleep)
        _done(scene, "avatar_download", db, r,
              {"sha256": (scene.avatar_video_sha256 or "")[:16]})
        return scene
    except Exception as e:  # recorded, then raised to the caller
        _fail(scene, step, e, db)
        raise
    finally:
        scene.running_since = None
        db.commit()


def scene_dict(s: HostScene) -> dict:
    return {
        "id": s.id, "case_id": s.case_id, "story_version_id": s.story_version_id,
        "host_segments_id": s.host_segments_id, "host_segment_id": s.host_segment_id,
        "language": s.language, "channel": s.channel, "position": s.position,
        "beat_id": s.beat_id, "text": s.text, "text_sha256": s.text_sha256,
        "studio_profile_id": s.studio_profile_id, "studio_asset_id": s.studio_asset_id,
        "framing_preset": s.framing_preset, "host_position": _loads(s.host_position_json, {}),
        "host_scale": s.host_scale, "background_mode": s.background_mode,
        "planned_start": s.planned_start, "planned_duration": s.planned_duration,
        "voice_id": s.voice_id, "voice_ready": bool(s.voice_path),
        "voice_seconds": s.voice_seconds, "voice_sha256": s.voice_sha256,
        "avatar_provider": s.avatar_provider, "avatar_id": s.avatar_id,
        "provider_job_id": s.provider_job_id, "provider_generation": s.provider_generation,
        "avatar_ready": bool(s.avatar_video_path), "avatar_video_sha256": s.avatar_video_sha256,
        "status": s.status, "failed_step": s.failed_step, "last_error": s.last_error,
        "attempts": _loads(s.attempts_json, {}), "history": _loads(s.history_json, []),
        "running": s.running_since is not None,
        "created_at": s.created_at, "updated_at": s.updated_at,
    }

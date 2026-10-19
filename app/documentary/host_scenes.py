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
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.env import env_value
from app.db.models import HostScene, HostSegments, StoryVersion
from app.documentary import storage
from app.documentary import stage_direction as SD
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
            planned_duration=seg.get("target_seconds"),
            voice_id=cp["elevenlabs_voice_id"], avatar_provider=ai_config.avatar.provider,
            persona_file=seg_row.persona_file, persona_sha256=seg_row.persona_sha256,
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


_SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+")


def _sentences(text: str) -> list[str]:
    return [x for x in _SENTENCE_END.split(" ".join(text.split())) if x]


def voice_flags(take: dict, listener_missing: bool, asr_missing: bool) -> list[str]:
    """What a person has to look at in a voiced appearance: a Whisper check that failed after
    the retake, words still said wrong after the fixes, a check that could not run."""
    flags = []
    asr = take.get("asr")
    if asr and not asr["passed"]:
        flags.append("asr_check_failed")
    flags += [f"pronunciation_unresolved:{r['word']}"
              for r in take.get("pronunciation") or [] if r.get("ok") is False]
    if listener_missing:
        flags.append("listener_unavailable")
    if asr_missing:
        flags.append("asr_unavailable")
    return flags


async def _pronunciation_key(db: Session, scene: HostScene, sentences: list[str], editor) -> dict:
    """The words of the appearance a voice could misread, with the reading the meaning needs
    (the same key as for the story's sentences). Kept per text, so a retake does not ask again."""
    checks = _loads(scene.voice_checks_json, {})
    key = checks.get("key") or {}
    if key.get("text_sha256") == scene.text_sha256:
        return {int(i): w for i, w in key["words"].items()}
    from app.documentary.pronunciation import PronunciationEditor

    items = [{"i": i, "beat_id": scene.host_segment_id, "text": t, "source": scene.text}
             for i, t in enumerate(sentences)]
    keyed, report = await (editor or PronunciationEditor()).annotate(
        db, scene.case_id, scene.language, items)
    checks["key"] = {"text_sha256": scene.text_sha256, "words": {str(i): w for i, w in keyed.items()},
                     "report": report}
    scene.voice_checks_json = json.dumps(checks, ensure_ascii=False)
    return keyed


async def _step_voice(db: Session, scene: HostScene, renderer=None, editor=None) -> dict | None:
    """ElevenLabs audio of exactly the frozen text, in the host's own style, through the same
    checks as the story's blocks: Whisper on the plain text, the pronunciation key and listening
    with the fix ladder (harakat only in the text sent to the voice), flags for a person.
    Reused when the saved file still matches text, voice, model and settings."""
    from app.documentary.voice_render import VoiceRenderer

    vcfg = ai_config.voice.languages.get(scene.language)
    if vcfg is None or not scene.voice_id:
        raise ST.StudioError(f"no voice configured for {scene.language}")
    meta = _loads(scene.voice_meta_json, {})
    # the host speaks in its own style (host.voice_style), not the narration's
    settings = ai_config.voice.styles[ai_config.host.voice_style].model_dump()
    p = storage.resolve(scene.voice_path) if scene.voice_path else None
    if (p is not None and p.exists() and meta.get("text_sha256") == scene.text_sha256
            and meta.get("voice_id") == scene.voice_id and meta.get("model_id") == vcfg.model_id
            and meta.get("settings") == settings and _sha(p.read_bytes()) == scene.voice_sha256
            and "flags" in _loads(scene.voice_checks_json, {})):
        return {"skipped": "saved audio matches the text, voice and settings"}
    from app.documentary.pronunciation import PronunciationEditor

    renderer = renderer or VoiceRenderer(db=db, budget_case_id=scene.case_id,
                                         budget_stage=f"tts:host:{scene.language}",
                                         arbiter=PronunciationEditor())
    sentences = _sentences(scene.text)
    pron = ai_config.pronunciation
    keyed = (await _pronunciation_key(db, scene, sentences, editor)
             if pron.enabled and scene.language in pron.languages else {})
    d = scene_dir(scene)
    block = {"block_id": f"host{scene.id}", "text": " ".join(sentences), "beats": [],
             "sentences": [{"speech": t, "display": t, "tts": t, "level": None,
                            "risky": keyed.get(i, [])} for i, t in enumerate(sentences)]}
    result = await renderer.speak_block(block, scene.language, vcfg, settings,
                                        ai_config.voice.seed, d / "takes",
                                        synonyms=False)
    take = result["take"]
    audio = Path(take["raw_path"]).read_bytes()
    name = f"voice_{scene.text_sha256[:12]}_{scene.voice_id[:8]}.mp3"
    _write_atomic(d / name, audio)
    al = take["alignment"]
    seconds = round(max(al["ends"]), 3) if al.get("ends") else None
    meta = {"text_sha256": scene.text_sha256, "voice_id": scene.voice_id,
            "model_id": vcfg.model_id, "settings": settings,
            "request_id": take.get("request_id"), "character_cost": take.get("character_cost"),
            "audio_format": ai_config.voice.elevenlabs.output_format, "seconds": seconds,
            "created_at": utc_now().isoformat()}
    _write_atomic(d / (name + ".json"), json.dumps(
        {**meta, "text": scene.text, "tts_text": take["text"],
         "alignment": {"characters": al["characters"], "starts": al["starts"],
                       "ends": al["ends"]}}, ensure_ascii=False).encode("utf-8"))
    checks = _loads(scene.voice_checks_json, {})
    checks.update({
        "tts_text": take["text"] if take["text"] != block["text"] else None,
        "asr": None if not take.get("asr") else {
            k: take["asr"].get(k) for k in ("passed", "word_error_rate", "failures", "heard_text")},
        "pronunciation": take.get("pronunciation") or [],
        "fixes": result["fixes"], "attempts": result["attempts"],
        "asr_engine": renderer.asr.name if renderer.asr else None,
        "listener": getattr(renderer._listener, "name", None) if renderer._listener else None,
        "flags": voice_flags(take, bool(keyed) and renderer._listener is None,
                             renderer.asr is None)})
    scene.voice_checks_json = json.dumps(checks, ensure_ascii=False)
    scene.voice_path = storage.rel(d / name)
    scene.voice_sha256 = _sha(audio)
    scene.voice_seconds = seconds
    scene.voice_meta_json = json.dumps(meta)
    # a new voice invalidates everything the provider made from the old one
    scene.provider_asset_id = None
    scene.provider_job_id = None
    scene.avatar_video_path = None
    scene.avatar_video_sha256 = None
    if ORDER.index(scene.status) > ORDER.index("voice_ready"):
        scene.status = "voice_ready"
    return None


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


def direction_controls(scene: HostScene) -> tuple[str | None, str | None]:
    """(motion_prompt, expressiveness) HeyGen can execute from the scene's
    stored direction: gestures, posture and eye contact become one natural-
    language prompt, the emotion's intensity a coarse expressiveness level.
    None, None for a scene without a direction."""
    if not scene.direction_json:
        return None, None
    from app.core.prompts import prompt

    cfg = ai_config.stage_direction
    d = json.loads(scene.direction_json)
    gestures = [cfg.gesture_phrases[a["action"]] for a in d.get("performance_timeline") or []
                if a.get("action") in cfg.gesture_phrases and a["action"] != "G01_NONE"]
    text = prompt("documentary/host/motion_prompt").format(
        posture=cfg.posture_phrases.get(d.get("posture"), "sits naturally"),
        eye_contact=str(d.get("eye_contact") or "DIRECT").lower().replace("_", " "),
        gestures=("Over the speech he " + ", then ".join(gestures)) if gestures
        else cfg.gesture_phrases["G01_NONE"].capitalize())
    intensity = float((d.get("emotion") or {}).get("intensity") or 0.0)
    low, high = cfg.expressiveness_thresholds
    return text, "low" if intensity < low else ("medium" if intensity < high else "high")


async def _step_avatar_request(db: Session, scene: HostScene, prov, look_id: str) -> dict | None:
    if (scene.provider_job_id and scene.provider_job_voice_sha == scene.voice_sha256):
        return {"skipped": f"job {scene.provider_job_id} exists"}
    meta = _loads(scene.avatar_meta_json, {})
    composited = scene.background_mode == "provider_composited"
    key = f"tc-host-{scene.id}-{(scene.voice_sha256 or '')[:12]}-g{scene.provider_generation}"
    motion_prompt, expressiveness = direction_controls(scene)
    job = await prov.create_video(
        look_id, scene.provider_asset_id, key,
        output_format="mp4" if composited else ai_config.avatar.output_format,
        resolution=ai_config.avatar.resolution,
        background_asset_id=meta.get("background_asset_id") if composited else None,
        title=f"{scene.channel} case {scene.case_id} {scene.host_segment_id}",
        **({"motion_prompt": motion_prompt, "expressiveness": expressiveness}
           if motion_prompt else {}))
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


class HostTooLong(ST.StudioError):
    """The real voice is longer than the appearance's cap."""


def deviation(seconds: float | None, planned: float | None) -> float:
    """How far the real seconds are from the planned ones, as a share of the planned."""
    if not seconds or not planned:
        return 0.0
    return abs(seconds - planned) / planned


def unresolved_words(scene: HostScene) -> list[str]:
    """Words the voice still says wrong after the whole fix ladder (from the saved checks)."""
    return [f.split(":", 1)[1] for f in _loads(scene.voice_checks_json, {}).get("flags", [])
            if f.startswith("pronunciation_unresolved:")]


async def _voice_within_plan(db: Session, scene: HostScene, renderer, retimer,
                             editor=None) -> dict | None:
    """Synthesize and check, measure the REAL seconds, and have the text rewritten (bounded) and
    voiced again when the voice misses the planned seconds by more than host.duration_tolerance
    or still says a word wrong after the vowel-mark ladder: the host's own words cannot be
    swapped for a synonym, so the writer says the same thing in other words. A voice still over
    the appearance's hard cap after that fails the scene."""
    r = await _step_voice(db, scene, renderer=renderer, editor=editor)
    attempts = 0
    tolerance = ai_config.host.duration_tolerance
    while ((deviation(scene.voice_seconds, scene.planned_duration) > tolerance
            or unresolved_words(scene))
           and retimer is not None and attempts < ai_config.host.max_retime_attempts):
        attempts += 1
        avoid = unresolved_words(scene)
        _log(scene, "voice", "retime", {"seconds": scene.voice_seconds,
                                        "planned": scene.planned_duration, "attempt": attempts,
                                        **({"avoid": avoid} if avoid else {})})
        await retimer(db, scene, scene.planned_duration, avoid)
        r = await _step_voice(db, scene, renderer=renderer, editor=editor)
    if (scene.voice_seconds or 0) > scene_cap(scene.position):
        raise HostTooLong(f"the host voice is {scene.voice_seconds}s, over the "
                          f"{scene_cap(scene.position)}s cap for a {scene.position} appearance")
    _record_real_seconds(db, scene)
    return r


def _record_real_seconds(db: Session, scene: HostScene) -> None:
    """The real voice seconds go into the stored segment next to the planned ones: the timeline
    is planned from text, then corrected from the measured audio."""
    row = db.get(HostSegments, scene.host_segments_id)
    segments = _loads(row.segments_json, [])
    for seg in segments:
        if seg.get("segment_id") == scene.host_segment_id:
            seg["real_seconds"] = scene.voice_seconds
            seg["seconds_deviation"] = round(deviation(scene.voice_seconds,
                                                       scene.planned_duration), 4)
    row.segments_json = json.dumps(segments, ensure_ascii=False)
    db.commit()


async def run_scene(db: Session, scene: HostScene, until: str = "voice",
                    renderer=None, avatar_provider=None, sleep=asyncio.sleep,
                    retimer=None, director=None, editor=None) -> HostScene:
    """Run the scene's steps up to `until` ("voice" or "avatar"), starting
    from the first one whose saved result is missing. Each step commits its
    result before the next begins; a failure is recorded and re-raised."""
    if until not in ("voice", "avatar"):
        raise ValueError("until must be 'voice' or 'avatar'")
    scene.running_since = utc_now()
    db.commit()
    step = "voice"
    try:
        r = await _voice_within_plan(db, scene, renderer, retimer, editor)
        _done(scene, "voice", db, r, {"voice_sha256": (scene.voice_sha256 or "")[:16],
                                      "seconds": scene.voice_seconds})
        if director is not None and not scene.direction_json:
            step = "stage_direction"
            await director(db, scene)
            _log(scene, "stage_direction", "ok", {"direction_sha256": (scene.direction_sha256 or "")[:16]})
            db.commit()
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
            configured = env_value(avatar_env) or ""
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
    from app.documentary.host import persona_state

    direction = _loads(s.direction_json, None)
    return {
        **persona_state(s),
        "direction": direction,
        "direction_sha256": s.direction_sha256,
        "direction_execution": SD.execution_view(direction),
        "id": s.id, "case_id": s.case_id, "story_version_id": s.story_version_id,
        "host_segments_id": s.host_segments_id, "host_segment_id": s.host_segment_id,
        "language": s.language, "channel": s.channel, "position": s.position,
        "beat_id": s.beat_id, "text": s.text, "text_sha256": s.text_sha256,
        "studio_profile_id": s.studio_profile_id, "studio_asset_id": s.studio_asset_id,
        "framing_preset": s.framing_preset, "host_position": _loads(s.host_position_json, {}),
        "host_scale": s.host_scale, "background_mode": s.background_mode,
        "planned_start": s.planned_start, "planned_duration": s.planned_duration,
        "voice_id": s.voice_id, "voice_ready": bool(s.voice_path),
        "voice_checks": {k: v for k, v in _loads(s.voice_checks_json, {}).items() if k != "key"},
        "voice_flags": _loads(s.voice_checks_json, {}).get("flags", []),
        "voice_seconds": s.voice_seconds, "voice_sha256": s.voice_sha256,
        "avatar_provider": s.avatar_provider, "avatar_id": s.avatar_id,
        "provider_job_id": s.provider_job_id, "provider_generation": s.provider_generation,
        "avatar_ready": bool(s.avatar_video_path), "avatar_video_sha256": s.avatar_video_sha256,
        "status": s.status, "failed_step": s.failed_step, "last_error": s.last_error,
        "attempts": _loads(s.attempts_json, {}), "history": _loads(s.history_json, []),
        "running": s.running_since is not None,
        "created_at": s.created_at, "updated_at": s.updated_at,
    }


def scene_cap(position: str | None) -> float:
    """The most real voice seconds a host appearance of this position may take."""
    cfg = ai_config.host
    hi = max((r.max_seconds for r in cfg.roles.values() if r.position == (position or "mid")),
             default=cfg.max_appearance_seconds)
    return min(float(hi), cfg.max_appearance_seconds)


def real_time_report(scenes: list[HostScene], narration_seconds: float,
                     intro_seconds: float = 0.0) -> dict:
    """Host time of a film measured on the REAL voice seconds (after TTS): planned against real
    seconds per appearance (a miss beyond host.duration_tolerance is a warning), each appearance
    within its cap, the total within host.max_total_seconds; the share is reported, and only an
    error for a film of 600 s or more. The film's total is the story audio plus the host audio
    plus the channel's logo video."""
    cfg = ai_config.host
    rows = [{"scene": s.id, "segment": s.host_segment_id, "position": s.position,
             "planned": s.planned_duration, "seconds": s.voice_seconds,
             "deviation": round(deviation(s.voice_seconds, s.planned_duration), 4),
             "cap": scene_cap(s.position)} for s in scenes]
    errors, warnings = [], []
    for r in rows:
        if r["seconds"] is None:
            warnings.append({"code": "no_real_voice_yet", "segment": r["segment"]})
        elif r["seconds"] > r["cap"]:
            errors.append({"code": "appearance_over_cap", "segment": r["segment"],
                           "seconds": r["seconds"], "cap": r["cap"]})
        elif r["deviation"] > cfg.duration_tolerance:
            warnings.append({"code": "seconds_off_plan", "segment": r["segment"],
                             "planned": r["planned"], "seconds": r["seconds"]})
    timed = [r for r in rows if r["seconds"] is not None]
    total = round(sum(r["seconds"] for r in timed), 2)
    if total > cfg.max_total_seconds:
        errors.append({"code": "host_time_over_cap", "seconds": total, "cap": cfg.max_total_seconds})
    share = round(total / max(narration_seconds, 1.0), 4)
    if share > cfg.max_total_share:
        (errors if narration_seconds >= 600 else warnings).append(
            {"code": "host_share_too_high", "share": share, "max": cfg.max_total_share})
    return {"scenes": rows, "host_seconds": total, "host_share": share,
            "timeline": film_timeline(narration_seconds, total, intro_seconds),
            "errors": errors, "warnings": warnings}


def film_timeline(story_seconds: float, host_seconds: float, intro_seconds: float = 0.0) -> dict:
    """The film's length from real durations: story audio + host audio + logo video."""
    return {"story_seconds": round(story_seconds, 2), "host_seconds": round(host_seconds, 2),
            "intro_seconds": round(intro_seconds, 2),
            "total_seconds": round(story_seconds + host_seconds + intro_seconds, 2)}

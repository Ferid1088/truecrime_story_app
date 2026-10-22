"""The real, once-generated music library for host-led reels (S7): one prompt per mood per
channel, generated once through Eleven Music, stored through the shared, content-addressed asset
store, and logged with full provenance (``HostLedMusicTrack``) so the owner has real evidence if
a licence or Content ID question ever comes up.

Selection (``select_track``) picks by mood, length and "not used in the last N clips of this
channel" (``music_library.no_repeat_window_reels``); a track the owner has marked
``content_id_status == "claimed"`` is never selected.

Nothing generated here is ever uploaded to a music distribution service on its own
(``forbid_standalone_distribution``) — only ever muxed into a reel/film."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import HostLedMusicTrack, HostLedMusicUsage
from app.documentary import audio as A
from app.providers.music.elevenlabs_music import ElevenLabsMusicProvider
from app.shortform.asset_store import SharedAssetStore
from app.utils import utc_now

log = logging.getLogger(__name__)

_MOOD_TEXT = {
    "searching": "a quiet, unresolved searching feeling, slow harmonic movement, no resolution",
    "waiting": "still, suspended, very little movement, patient and restrained",
    "revealing": "a small, controlled lift in intensity as if something is coming into view, still calm",
    "uneasy_calm": "calm on the surface with a faint unresolved tension underneath",
}

TERMS_URL = "https://elevenlabs.io/pricing (commercial-use terms, Music API), read 2026-10-20"
TERMS_RECORDED_DATE = "2026-10-20"
LICENCE_NOTE = (
    "Starter/Pro tiers: individual use only. Scale/Business: streaming/web content and online "
    "advertising only. Film/TV use prohibited except on Enterprise. Attribution 'Created in "
    "collaboration with ElevenLabs' required on all plans except Enterprise. YouTube/Instagram/"
    "TikTok/Facebook are not named anywhere in the terms; monetization and Content ID are not "
    "addressed. Recorded from the owner's own reading of the vendor pages, not a page that "
    "states Eleven Music's exact Content ID/monetization policy verbatim."
)

# A pure-music distribution target: generated tracks are never exported to one of these on
# their own, only ever muxed into a reel/film.
_DISTRIBUTION_SERVICE_MARKERS = (
    "spotify", "apple_music", "applemusic", "distrokid", "tunecore", "cdbaby",
    "soundcloud_upload", "amazon_music",
)


class StandaloneMusicDistributionForbidden(RuntimeError):
    pass


def forbid_standalone_distribution(target: str) -> None:
    """Raise if ``target`` (a publish destination identifier, platform name, or URL) looks like
    a pure-music distribution service rather than a video reel/film platform."""
    low = target.lower()
    for marker in _DISTRIBUTION_SERVICE_MARKERS:
        if marker in low:
            raise StandaloneMusicDistributionForbidden(
                f"refusing to export generated music to {target!r}: it is never uploaded alone "
                "to a music distribution service, only muxed into a reel/film")


class MusicPromptForbidden(RuntimeError):
    pass


class MusicGenerationDisabled(RuntimeError):
    """``shorts_host_led.music_provider.allow_generation`` is false and no stored track fits:
    generation is refused rather than silently falling back to a paid call."""


def validate_music_prompt(prompt: str) -> None:
    """A prompt may never name an artist/band/song/film/franchise or ask for 'in the style of' —
    enforced here via the config-driven banned-phrase list (owner-maintained, not exhaustive, but
    every prompt this code sends is checked against it before any paid call)."""
    low = prompt.lower()
    for phrase in ai_config.shorts_host_led.music_library.banned_prompt_phrases:
        if phrase.lower() in low:
            raise MusicPromptForbidden(f"prompt contains forbidden phrase {phrase!r}: {prompt!r}")


def _mood_prompt(channel: str, mood: str) -> str:
    family = ai_config.shorts_host_led.music_library.channel_mood_family[channel]
    return prompt("shortform/host_led_music_bed").format(family=family, mood_text=_MOOD_TEXT[mood])


def generate_library(db: Session, channel: str, *, root: Path | None = None) -> list[HostLedMusicTrack]:
    """Generate the real library for one channel/language, once, through the shared asset store.
    A mood already present as a non-excluded track for this channel is not regenerated.
    When ``shorts_host_led.music_provider.allow_generation`` is false, a missing mood is never
    generated: ``MusicGenerationDisabled`` is raised naming the channel and mood instead of
    silently falling back to a paid call."""
    cfg = ai_config.shorts_host_led
    store = SharedAssetStore(db, root=root)
    tracks: list[HostLedMusicTrack] = []
    for mood in cfg.music_library.moods[: cfg.music_library.max_tracks_per_language]:
        existing = (db.query(HostLedMusicTrack)
                    .filter_by(channel=channel, mood=mood, excluded=False).first())
        if existing is not None:
            tracks.append(existing)
            continue
        if not cfg.music_provider.allow_generation:
            raise MusicGenerationDisabled(
                f"no stored track for channel={channel!r} mood={mood!r} and "
                "shorts_host_led.music_provider.allow_generation is false: refusing to "
                "generate a new one")
        provider = ElevenLabsMusicProvider()
        prompt = _mood_prompt(channel, mood)
        validate_music_prompt(prompt)
        length_ms = int(cfg.music_library.track_length_seconds * 1000)

        generated: dict[str, str | None] = {"generation_id": None}

        def _create(path: Path, _prompt=prompt, _length_ms=length_ms, _provider=provider,
                    generated=generated) -> None:
            result = _provider.generate(_prompt, length_ms=_length_ms)
            path.write_bytes(result.audio_bytes)
            generated["generation_id"] = result.provider_generation_id

        path, created = store.get_or_create(
            "music", channel, {"mood": mood, "prompt": prompt, "length_ms": length_ms},
            cfg.template_version, _create)
        duration = A.probe_duration(path)
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        generation_id = generated["generation_id"] if created else None
        from app.shortform.asset_store import asset_key
        key = asset_key("music", channel, {"mood": mood, "prompt": prompt, "length_ms": length_ms},
                        cfg.template_version)
        track = HostLedMusicTrack(
            channel=channel, mood=mood, prompt=prompt, provider=cfg.music_provider.name,
            model_id=cfg.music_provider.model_id, provider_generation_id=generation_id,
            plan_name=cfg.music_library.plan_name, duration_seconds=duration,
            bpm_estimate=None, bpm_method=None, file_path=str(path.relative_to(store.root)),
            file_sha256=file_hash, licence_note=LICENCE_NOTE, terms_url=TERMS_URL,
            terms_recorded_date=TERMS_RECORDED_DATE, asset_store_key=key,
        )
        db.add(track)
        db.commit()
        db.refresh(track)
        # Evidence sidecar next to the audio file, same fields as the DB row.
        import json
        sidecar = path.with_suffix(path.suffix + ".provenance.json")
        sidecar.write_text(json.dumps({
            "channel": channel, "mood": mood, "prompt": prompt, "provider": track.provider,
            "model_id": track.model_id, "provider_generation_id": track.provider_generation_id,
            "plan_name": track.plan_name, "duration_seconds": track.duration_seconds,
            "file_sha256": track.file_sha256, "licence_note": track.licence_note,
            "terms_url": track.terms_url, "terms_recorded_date": track.terms_recorded_date,
            "generated_at": track.created_at.isoformat(),
        }, indent=2), encoding="utf-8")
        tracks.append(track)
    return tracks


def _eligible_tracks(db: Session):
    return (db.query(HostLedMusicTrack)
            .filter(HostLedMusicTrack.excluded.is_(False),
                    HostLedMusicTrack.content_id_status != "claimed")
            .order_by(HostLedMusicTrack.id))


def pinned_track(db: Session) -> HostLedMusicTrack | None:
    """The one track every short and reel uses, in every language: the oldest eligible track of
    ``music_library.pinned_mood``. ``None`` when no mood is pinned or no eligible track has it."""
    mood = ai_config.shorts_host_led.music_library.pinned_mood
    if mood is None:
        return None
    return _eligible_tracks(db).filter(HostLedMusicTrack.mood == mood).first()


def select_track(db: Session, channel: str, case_id: int, *, mood: str | None = None) -> HostLedMusicTrack | None:
    """The track for one reel: the pinned track when a mood is pinned (the same music in every
    language and for every reel), else a track of the channel that is not excluded, not Content-ID-claimed
    and not used in any of the case's last ``no_repeat_window_reels`` reels (whichever mood
    matches, else any mood)."""
    pinned = pinned_track(db)
    if pinned is not None:
        return pinned
    window = ai_config.shorts_host_led.music_library.no_repeat_window_reels
    recent_ids = {row.track_id for row in (
        db.query(HostLedMusicUsage).filter_by(case_id=case_id)
        .order_by(HostLedMusicUsage.id.desc()).limit(window).all())}
    q = _eligible_tracks(db).filter_by(channel=channel)
    if mood:
        q = q.filter_by(mood=mood)
    candidates = [t for t in q.all() if t.id not in recent_ids]
    if not candidates and mood:
        return select_track(db, channel, case_id, mood=None)
    if not candidates:
        return None
    return candidates[0]


def record_usage(db: Session, track: HostLedMusicTrack, case_id: int, reel_key: str) -> None:
    db.add(HostLedMusicUsage(track_id=track.id, case_id=case_id, reel_key=reel_key, used_at=utc_now()))
    db.commit()
    try:
        from app.documentary.music_cost import book_host_led
        book_host_led(db, track, case_id)
    except Exception as e:  # noqa: BLE001 — cost booking never breaks a run
        log.warning("host-led music cost booking failed for track %s: %s", track.id, e)


def mark_content_id_checked(db: Session, track: HostLedMusicTrack, *, status: str, result: str) -> None:
    """The manual checklist step: 'upload once as private/unlisted on YouTube and check Content
    ID/copyright status before publishing'. ``status`` is 'clear' or 'claimed'; a claimed track
    is excluded from selection from this point on."""
    if status not in ("clear", "claimed"):
        raise ValueError("status must be 'clear' or 'claimed'")
    track.content_id_status = status
    track.content_id_checked = True
    track.content_id_checked_at = utc_now()
    track.content_id_result = result
    if status == "claimed":
        track.excluded = True
        track.excluded_reason = f"Content ID claim: {result}"
    db.commit()


def tracks_needed_for_window(reels: int, window: int) -> int:
    """The minimum distinct tracks so a run of ``reels`` reels never repeats a track within any
    window of ``window`` consecutive reels: exactly ``window`` distinct tracks suffice (round-
    robin through them), and fewer than ``window`` cannot avoid a repeat inside some window of
    that size once ``reels >= window``."""
    if reels < window:
        return min(reels, window)
    return window

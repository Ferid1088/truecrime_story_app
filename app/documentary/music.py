"""Music library, track selection and the documentary mix.

MusicLibrary — one shared, growing library of tracks (MusicTrack). A
track is (kind, mood, variant): kind = bridge | sting | bed | room_tone,
mood from audio_direction.moods, and a variant is one instrumentation
from music_library.variant_styles. Its prompt is the kind's template +
music_library.mood_prompts[mood] + that style. Tracks are generated
lazily (ElevenLabs sound generation) the first time a film needs them
and normalized to one reference loudness, so mix levels stay
predictable. The cues named in config (music_library.cues) are imported
as variant-1 tracks and keep the files already generated for them.

Track selection (select_track) — every film should sound like itself,
and no two recent films like each other:
  1. Within a film (film_key — one film across all its languages) a
     (kind, mood) keeps its track: the film's theme. Every language of
     the film hears the same music.
  2. Across films, a track used by one of the last
     music_library.reuse_after_videos OTHER films is skipped; among the
     rest the least-used wins.
  3. When nothing fresh exists, a new variant is generated while the
     (kind, mood) has fewer than music_library.max_variants_per_mood.
  4. Only then does the least recently used track return — and the
     selection reason says so.
  Room tone is not music: one room tone serves every film.
Every placement is stored as a MusicUsage row with the director's reason
(why) and the selection reason, so "why this cue / this silence?" is
answered from the database.

plan_placements — where each cue sits on the narration's real timeline.
With audio_direction.beds_under_narration false (the default) nothing
plays while the narrator speaks: a cue starts
music_start_after_word_seconds after the last word of a beat and has
faded out music_end_before_word_seconds before the next word; a
director's silence is room tone in the gap. With beds allowed, the older
layout applies: beds under consecutive beats with the same mood, and
moments that rise under the last words and fade under the next beat's
first words.

DocumentaryMixer — chooses the tracks, records their use, lays them
under the narration and normalizes the whole mix to the narration target
loudness.
"""

from __future__ import annotations

import asyncio
import bisect
import hashlib
import json
import shutil
import weakref
from pathlib import Path

from sqlalchemy import distinct, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.ai_config import MusicCue, ai_config
from app.db.base import SessionLocal
from app.db.models import MusicTrack, MusicUsage, StoryVersion
from app.documentary import audio as A
from app.documentary.audio_director import normalize_mood

ROOT = Path(__file__).resolve().parents[2]
MUSIC_TYPES = {"music_bridge", "emotional_moment", "chapter_break", "sting"}

# The library kind that serves each placement role.
TRACK_KIND = {
    "music_bridge": "bridge", "emotional_moment": "bridge", "chapter_break": "bridge",
    "sting": "sting", "silence": "room_tone", "bed": "bed",
}
ROOM_TONE_MOOD = "neutral"

# Prompt per kind: {mood} = music_library.mood_prompts[mood], {style} =
# one of music_library.variant_styles. Wording follows the hand-written
# config cues that sounded right; lengths stay inside the provider's
# ~22 s limit (loops cover longer gaps).
KIND_TEMPLATES: dict[str, dict] = {
    "bridge": {
        "seconds": 22.0, "loop": True,
        "prompt": "seamless loop, cinematic interlude for a true crime documentary, "
                  "{mood}, {style}, slowly evolving, no vocals",
    },
    "sting": {
        "seconds": 4.0, "loop": False,
        "prompt": "single short documentary accent after a turn in the story, {mood}, "
                  "{style}, one deep low hit with a long dark reverb tail, no vocals",
    },
    "bed": {
        "seconds": 20.0, "loop": True,
        "prompt": "seamless loop, very quiet documentary underscore to sit under a "
                  "narrator, {mood}, {style}, slow and patient, no percussion, no vocals",
    },
    "room_tone": {
        "seconds": 20.0, "loop": True,
        "prompt": "seamless loop, very quiet room tone, soft air, faint distant hum, "
                  "no voices, no music",
    },
}

# Clean-gap placement (beds_under_narration false). A gap shorter than
# this cannot hold a cue that fades in and out without feeling clipped.
MIN_CUE_SECONDS = {"sting": 0.8, "music": 1.5, "silence": 0.3}
# Fades of a cue inside a gap: rise after the last word, gone before the
# next one (capped to a third of the cue so short cues still breathe).
GAP_FADE_IN_SECONDS = 1.0
GAP_FADE_OUT_SECONDS = 2.5
STING_FADE_OUT_SECONDS = 1.2
ROOM_TONE_FADE_SECONDS = 0.3


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def track_code(kind: str, mood: str, variant: int) -> str:
    return f"{kind.replace('_', '-')}-{mood}-v{variant}"


def _cue_identity(cue: MusicCue) -> tuple[str, str]:
    """(kind, mood) of a config cue in the current mood catalogue."""
    if cue.kind == "room_tone":
        return "room_tone", ROOM_TONE_MOOD
    return cue.kind, normalize_mood(cue.mood)


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------


class MusicLibrary:
    def __init__(self, provider=None):
        self.cfg = ai_config.music_library
        self._provider = provider

    @property
    def provider(self):
        if self._provider is None:
            from app.providers.voice.elevenlabs import ElevenLabsSoundProvider

            self._provider = ElevenLabsSoundProvider()
        return self._provider

    @property
    def dir(self) -> Path:
        d = Path(self.cfg.dir)
        return d if d.is_absolute() else ROOT / d

    def _key(self, cue) -> str:
        """Content key of a cue or track: same sound request, same file."""
        payload = json.dumps({"prompt": cue.prompt, "seconds": cue.seconds,
                              "loop": cue.loop,
                              "influence": self.cfg.prompt_influence,
                              "ref": self.cfg.reference_lufs}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def path_for(self, cue: MusicCue) -> Path:
        """File of a config cue (the name used before the track library)."""
        return self.dir / f"{cue.id}__{self._key(cue)}.wav"

    def track_path(self, track: MusicTrack) -> Path:
        """A track's file inside the CURRENT library dir (found by name, so
        a moved library keeps its files)."""
        name = (Path(track.file_path).name if track.file_path
                else f"{track.track_code}__{self._key(track)}.wav")
        return self.dir / name

    async def _generate(self, prompt: str, seconds: float, loop: bool, path: Path) -> int:
        self.dir.mkdir(parents=True, exist_ok=True)
        audio, cost = await self.provider.generate(
            prompt, seconds, loop, self.cfg.prompt_influence)
        raw = path.with_suffix(".mp3")
        raw.write_bytes(audio)
        await asyncio.to_thread(
            A.normalize_loudness, raw, path, self.cfg.reference_lufs,
            ai_config.loudness.true_peak_db, ai_config.loudness.lra,
            ai_config.loudness.sample_rate, 2,
        )
        return int(cost or 0)

    async def ensure_track(self, db: Session, track: MusicTrack) -> tuple[Path, int | None]:
        """Path of a track's normalized file; generated on first use
        (characters paid, None when the file already existed)."""
        path = self.track_path(track)
        if path.exists():
            return path, None
        cost = await self._generate(track.prompt, track.seconds, track.loop, path)
        track.file_path = _rel(path)
        track.characters_paid = (track.characters_paid or 0) + cost
        track.provider = getattr(self.provider, "name", None) or self.cfg.provider
        db.commit()
        return path, cost

    # --- catalogue ----------------------------------------------------------

    def _next_variant(self, db: Session, kind: str, mood: str) -> int:
        top = db.query(func.max(MusicTrack.variant)).filter(
            MusicTrack.kind == kind, MusicTrack.mood == mood).scalar()
        return int(top or 0) + 1

    def sync_config_cues(self, db: Session) -> None:
        """Import the config cues as tracks (once): the hand-written cues
        become variant 1 of their (kind, mood) and keep their files."""
        known = {(t.kind, t.mood, t.prompt) for t in db.query(MusicTrack).all()}
        added = False
        for cue in self.cfg.cues:
            kind, mood = _cue_identity(cue)
            if (kind, mood, cue.prompt) in known:
                continue
            variant = self._next_variant(db, kind, mood)
            db.add(MusicTrack(
                track_code=track_code(kind, mood, variant), kind=kind, mood=mood,
                variant=variant, seconds=cue.seconds, loop=cue.loop, prompt=cue.prompt,
                style=None, provider=self.cfg.provider, file_path=_rel(self.path_for(cue)),
            ))
            db.flush()
            known.add((kind, mood, cue.prompt))
            added = True
        if added:
            try:
                db.commit()
            except IntegrityError:  # another process imported them first
                db.rollback()

    def _style_for(self, kind: str, mood: str, used: set[str], variant: int) -> str | None:
        """An instrumentation this (kind, mood) does not have yet. Each
        (kind, mood) starts at its own place in the style list, so the
        first variants of different moods do not all sound alike."""
        styles = list(self.cfg.variant_styles)
        if not styles or kind == "room_tone":
            return None
        off = int(hashlib.sha256(f"{kind}|{mood}".encode()).hexdigest()[:6], 16) % len(styles)
        rotated = styles[off:] + styles[:off]
        unused = [s for s in rotated if s not in used]
        return unused[0] if unused else rotated[(variant - 1) % len(rotated)]

    def new_variant(self, db: Session, kind: str, mood: str) -> MusicTrack:
        """Register the next variant of (kind, mood); its file is generated
        on first use (ensure_track)."""
        variants = db.query(MusicTrack).filter(
            MusicTrack.kind == kind, MusicTrack.mood == mood).all()
        n = max((t.variant for t in variants), default=0) + 1
        style = self._style_for(kind, mood, {t.style for t in variants if t.style}, n)
        tpl = KIND_TEMPLATES[kind]
        mood_text = self.cfg.mood_prompts.get(mood) or f"{mood} documentary underscore"
        prompt = tpl["prompt"].format(mood=mood_text, style=style or "")
        track = MusicTrack(
            track_code=track_code(kind, mood, n), kind=kind, mood=mood, variant=n,
            seconds=tpl["seconds"], loop=tpl["loop"], prompt=prompt, style=style,
            provider=self.cfg.provider,
        )
        track.file_path = _rel(self.dir / f"{track.track_code}__{self._key(track)}.wav")
        db.add(track)
        try:
            db.commit()
        except IntegrityError:  # another process registered this variant
            db.rollback()
            return db.query(MusicTrack).filter(
                MusicTrack.track_code == track_code(kind, mood, n)).one()
        return track


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def recent_other_films(db: Session, film_key: str, n: int) -> list[str]:
    """The last n OTHER films, newest first (by their first MusicUsage)."""
    if n <= 0:
        return []
    first = func.min(MusicUsage.id)
    rows = (db.query(MusicUsage.film_key)
            .filter(MusicUsage.film_key != film_key)
            .group_by(MusicUsage.film_key)
            .order_by(first.desc())
            .limit(n).all())
    return [r[0] for r in rows]


def select_track(db: Session, library: MusicLibrary, kind: str, mood: str,
                 film_key: str) -> tuple[MusicTrack, str]:
    """The track for one (kind, mood) in one film, and why it was chosen."""
    cfg = ai_config.music_library
    theme = (db.query(MusicTrack)
             .join(MusicUsage, MusicUsage.track_id == MusicTrack.id)
             .filter(MusicUsage.film_key == film_key, MusicTrack.kind == kind,
                     MusicTrack.mood == mood, MusicTrack.active.is_(True))
             .order_by(MusicUsage.id).first())
    if theme is not None:
        return theme, (f"{theme.track_code}: film theme — {film_key} already uses it for "
                       f"{kind}/{mood} (same track in every language of the film)")
    tracks = (db.query(MusicTrack)
              .filter(MusicTrack.kind == kind, MusicTrack.mood == mood,
                      MusicTrack.active.is_(True))
              .order_by(MusicTrack.variant).all())
    if kind == "room_tone":
        if tracks:
            return tracks[0], (f"{tracks[0].track_code}: room tone is not music — one "
                               "room tone serves every film")
        t = library.new_variant(db, kind, mood)
        return t, f"{t.track_code}: first room tone in the library (serves every film)"

    ids = [t.id for t in tracks]
    films: dict[int, int] = {}
    last: dict[int, int] = {}
    if ids:
        for tid, n_films, last_id in (
                db.query(MusicUsage.track_id, func.count(distinct(MusicUsage.film_key)),
                         func.max(MusicUsage.id))
                .filter(MusicUsage.track_id.in_(ids))
                .group_by(MusicUsage.track_id).all()):
            films[tid], last[tid] = int(n_films), int(last_id)
    window = cfg.reuse_after_videos
    recent = recent_other_films(db, film_key, window)
    blocked: set[int] = set()
    if recent and ids:
        blocked = {tid for (tid,) in db.query(MusicUsage.track_id).filter(
            MusicUsage.film_key.in_(recent), MusicUsage.track_id.in_(ids)).distinct()}
    skipped = [t.track_code for t in tracks if t.id in blocked]
    skipped_note = (f"; skipped {', '.join(skipped)} (used by the last {window} other films)"
                    if skipped else "")

    fresh = [t for t in tracks if t.id not in blocked]
    if fresh:
        best = min(fresh, key=lambda t: (films.get(t.id, 0), last.get(t.id, 0), t.variant))
        used = films.get(best.id, 0)
        how = ("never used by another film" if not used else
               f"not used by the last {window} other films; least used ({used} films so far)")
        return best, f"{best.track_code}: {how}{skipped_note}"
    if len(tracks) < cfg.max_variants_per_mood:
        t = library.new_variant(db, kind, mood)
        why = (f"first {kind}/{mood} track in the library" if not tracks else
               f"all {len(tracks)} {kind}/{mood} variants were used by the last {window} "
               f"other films")
        return t, f"{t.track_code}: new variant generated — {why}{skipped_note}"
    best = min(tracks, key=lambda t: (last.get(t.id, 0), t.variant))
    last_film = None
    if best.id in last:
        row = db.get(MusicUsage, last[best.id])
        last_film = row.film_key if row else None
    return best, (f"{best.track_code}: REUSED — every {kind}/{mood} variant "
                  f"({len(tracks)} of max {cfg.max_variants_per_mood}) was used by the last "
                  f"{window} other films; least recently used"
                  + (f" (last in {last_film})" if last_film else ""))


def _blueprint_id_for_version(db: Session, story_version_id: int) -> int | None:
    """The editorial blueprint a version is told from. Spoken language
    versions name their master's blueprint; a master has its own."""
    from app.documentary.blueprint import latest_blueprint

    version = db.get(StoryVersion, story_version_id)
    if version is None:
        return None
    try:
        struct = json.loads(version.narrative_structure or "{}")
    except (ValueError, TypeError):
        struct = {}
    if isinstance(struct, dict) and struct.get("blueprint_id"):
        return int(struct["blueprint_id"])
    row = latest_blueprint(db, version.id)
    return row.id if row else None


def film_key_for(db: Session | None, script: dict, manifest: dict) -> tuple[str, int | None]:
    """(film_key, blueprint id). All languages of one film are told from
    one blueprint, so f"bp{id}" names the film across its languages; the
    story version is the fallback."""
    bp = script.get("blueprint_id") or manifest.get("blueprint_id")
    sv = manifest.get("story_version_id") or script.get("story_version_id")
    if not bp and sv and db is not None:
        bp = _blueprint_id_for_version(db, int(sv))
    if bp:
        return f"bp{int(bp)}", int(bp)
    return (f"sv{sv}" if sv else "film-unknown"), None


def _director_reasons(db: Session, blueprint_id: int | None) -> dict[str, str]:
    """beat_id → the director's reason, from the stored audio plan (for
    performance scripts made before reasons travelled with the plan)."""
    from app.documentary.audio_director import latest_audio_plan

    row = latest_audio_plan(db, blueprint_id) if blueprint_id else None
    if row is None:
        return {}
    try:
        plan = json.loads(row.plan_json or "{}")
    except (ValueError, TypeError):
        return {}
    return {pb["beat_id"]: (pb.get("after") or {}).get("why") or pb.get("why") or ""
            for pb in plan.get("beats") or [] if pb.get("beat_id")}


def record_usages(db: Session, *, case_id: int, film_key: str, language: str | None,
                  placements: list[dict]) -> None:
    """One MusicUsage per placement. A re-mix of the same film and
    language replaces its rows (no duplicates); other languages keep
    theirs."""
    q = db.query(MusicUsage).filter(MusicUsage.film_key == film_key)
    q = q.filter(MusicUsage.language == language) if language is not None else \
        q.filter(MusicUsage.language.is_(None))
    q.delete(synchronize_session="fetch")
    for p in placements:
        db.add(MusicUsage(
            track_id=p.get("track_id"), case_id=case_id, film_key=film_key,
            language=language,
            beat_id=p.get("after_beat") or (p.get("beats") or [None])[0],
            purpose=p["role"], mood=p.get("mood"),
            start=p["start"], end=round(p["start"] + p["duration"], 3),
            why=p.get("why") or None, selection_reason=p.get("selection_reason"),
        ))
    db.commit()


# ---------------------------------------------------------------------------
# placement on the narration timeline
# ---------------------------------------------------------------------------


def _reason(ba: dict) -> str:
    return str((ba.get("after") or {}).get("why") or ba.get("why") or "")


def _word_index(manifest: dict) -> tuple[list[float], list[float]]:
    """Word starts (sorted) and, per position, the latest end so far."""
    words = sorted(
        (float(w["start"]), float(w["end"]))
        for w in (manifest.get("timeline") or {}).get("words") or []
        if w.get("start") is not None and w.get("end") is not None
    )
    starts, reach, top = [], [], float("-inf")
    for s, e in words:
        top = max(top, e)
        starts.append(s)
        reach.append(top)
    return starts, reach


def speech_gap(starts: list[float], reach: list[float], beat_end: float,
               next_start: float) -> tuple[float, float]:
    """The silent stretch after a beat on the real word timeline: from the
    last word spoken before it to the first word after it. Words in the
    first half of the nominal gap belong to the ending beat, words in the
    second half to the next one."""
    lo, hi = beat_end, next_start
    i = bisect.bisect_left(starts, (beat_end + next_start) / 2)
    if i:
        lo = max(lo, reach[i - 1])
    if i < len(starts):
        hi = min(hi, starts[i])
    return lo, hi


def _clean_moments(spans, beat_audio, manifest, direction) -> tuple[list[dict], list[dict]]:
    """Cues inside the narration's pauses only — never under a word."""
    starts, reach = _word_index(manifest)
    out: list[dict] = []
    skipped: list[dict] = []
    after_word = direction.music_start_after_word_seconds
    before_word = direction.music_end_before_word_seconds
    for s, nxt in zip(spans, spans[1:]):
        ba = beat_audio.get(s["beat_id"], {})
        after = ba.get("after") or {}
        kind = after.get("type")
        if kind not in MUSIC_TYPES | {"silence"}:
            continue
        lo, hi = speech_gap(starts, reach, float(s["end"]), float(nxt["start"]))
        if kind == "silence":
            dur = hi - lo
            if dur < MIN_CUE_SECONDS["silence"]:
                skipped.append({"role": kind, "after_beat": s["beat_id"],
                                "reason": f"gap_too_short ({max(dur, 0):.2f} s)"})
                continue
            out.append({"role": "silence", "cue_kind": "silence", "mood": ROOM_TONE_MOOD,
                        "after_beat": s["beat_id"], "start": round(lo, 3),
                        "duration": round(dur, 3),
                        "fade_in": min(ROOM_TONE_FADE_SECONDS, dur / 3),
                        "fade_out": min(ROOM_TONE_FADE_SECONDS, dur / 3),
                        "level_db": direction.room_tone_level_db, "why": _reason(ba)})
            continue
        sting = kind == "sting"
        start, end = lo + after_word, hi - before_word
        dur = end - start
        if dur < MIN_CUE_SECONDS["sting" if sting else "music"]:
            skipped.append({"role": kind, "after_beat": s["beat_id"],
                            "reason": f"gap_too_short ({max(hi - lo, 0):.2f} s)"})
            continue
        out.append({
            "role": kind, "cue_kind": "sting" if sting else kind,
            "mood": normalize_mood(after.get("mood"), kind), "after_beat": s["beat_id"],
            "start": round(start, 3), "duration": round(dur, 3),
            "fade_in": 0.02 if sting else round(min(GAP_FADE_IN_SECONDS, dur / 3), 3),
            "fade_out": round(min(STING_FADE_OUT_SECONDS if sting else GAP_FADE_OUT_SECONDS,
                                  dur / (2 if sting else 3)), 3),
            "level_db": direction.sting_level_db if sting else direction.moment_level_db,
            "why": _reason(ba),
        })
    return out, skipped


def _overlapping_placements(spans, beat_audio, total, direction) -> list[dict]:
    """The layout used while beds may sit under the narration: beds under
    consecutive beats, moments that rise under the last words and fade
    under the next beat's first words."""
    gaps = {}
    for a, b in zip(spans, spans[1:]):
        gaps[a["beat_id"]] = max(b["start"] - a["end"], 0.0)

    out: list[dict] = []
    # beds: one placement per run of beats with the same mood and level
    run: list[dict] = []

    def flush():
        if not run:
            return
        ba = beat_audio.get(run[0]["beat_id"], {})
        last = run[-1]
        after = (beat_audio.get(last["beat_id"], {}).get("after") or {}).get("type")
        end = last["end"] + (0.6 if after in MUSIC_TYPES else gaps.get(last["beat_id"], 0.0))
        start = max(run[0]["start"] - 0.8, 0.0)
        dur = min(end, total) - start
        if dur > 1.0:
            out.append({
                "role": "bed", "cue_kind": "bed", "mood": normalize_mood(ba.get("bed")),
                "beats": [r["beat_id"] for r in run],
                "start": round(start, 3), "duration": round(dur, 3),
                "fade_in": min(2.0, dur / 3), "fade_out": min(2.5, dur / 3),
                "level_db": direction.bed_levels_db.get(ba.get("bed_level"), -26.0),
                "why": _reason(ba) or "bed under narration (beds_under_narration=true)",
            })
        run.clear()

    for s in spans:
        ba = beat_audio.get(s["beat_id"], {})
        key = (ba.get("bed"), ba.get("bed_level"))
        if ba.get("bed") in (None, "none"):
            flush()
            continue
        if run:
            prev = beat_audio.get(run[-1]["beat_id"], {})
            if (prev.get("bed"), prev.get("bed_level")) != key:
                flush()
        run.append(s)
    flush()

    # moments in the gap after a beat
    for s in spans:
        ba = beat_audio.get(s["beat_id"], {})
        after = ba.get("after") or {}
        kind = after.get("type")
        gap = gaps.get(s["beat_id"])
        if gap is None or kind not in MUSIC_TYPES | {"silence"}:
            continue
        if kind == "silence":
            out.append({"role": "silence", "cue_kind": "silence", "mood": ROOM_TONE_MOOD,
                        "after_beat": s["beat_id"], "start": round(s["end"], 3),
                        "duration": round(gap, 3), "fade_in": 0.3, "fade_out": 0.3,
                        "level_db": direction.room_tone_level_db, "why": _reason(ba)})
            continue
        sting = kind == "sting"
        # Music moments rise softly under the beat's last words and keep
        # playing under the next beat's first words (never a hard stop).
        lead = 0.0 if sting else min(direction.music_lead_seconds, s["end"] - s["start"])
        tail = 2.0 if sting else direction.music_tail_seconds
        start = max(s["end"] - lead, 0.0)
        dur = min(gap + lead + tail, total - start)
        out.append({
            "role": kind, "cue_kind": "sting" if sting else kind,
            "mood": normalize_mood(after.get("mood"), kind), "after_beat": s["beat_id"],
            "start": round(start, 3), "duration": round(dur, 3),
            "fade_in": 0.02 if sting else round(lead + 0.4, 3),
            "fade_out": 1.2 if sting else round(max(tail, 1.5), 3),
            "level_db": direction.sting_level_db if sting else direction.moment_level_db,
            "why": _reason(ba),
        })
    return out


def _plan(manifest: dict, script: dict) -> tuple[list[dict], list[dict]]:
    direction = ai_config.audio_direction
    beat_audio = script.get("beat_audio") or {}
    spans = sorted(manifest["timeline"].get("beats") or [], key=lambda b: b["start"])
    if not spans:
        return [], []
    if direction.beds_under_narration:
        total = manifest.get("duration_seconds") or spans[-1]["end"]
        out, skipped = _overlapping_placements(spans, beat_audio, total, direction), []
    else:
        out, skipped = _clean_moments(spans, beat_audio, manifest, direction)
    return sorted(out, key=lambda p: p["start"]), skipped


def plan_placements(manifest: dict, script: dict) -> list[dict]:
    """Where each cue goes on the narration timeline (seconds)."""
    return _plan(manifest, script)[0]


# ---------------------------------------------------------------------------
# mix
# ---------------------------------------------------------------------------

_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _library_lock() -> asyncio.Lock:
    """One track selection at a time (per event loop): the languages of a
    film are mixed in parallel and must agree on the film's tracks — and
    must never generate the same new variant twice."""
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = _LOCKS[loop] = asyncio.Lock()
    return lock


class DocumentaryMixer:
    def __init__(self, library: MusicLibrary | None = None):
        self.library = library or MusicLibrary()
        self.loud = ai_config.loudness

    async def assign_tracks(self, manifest: dict, script: dict,
                            db: Session | None = None) -> dict:
        """Plan the placements, choose a library track for each, make sure
        its file exists and record the use (MusicUsage). No audio is mixed.
        Without a session one is opened (and closed) here."""
        placements, skipped = _plan(manifest, script)
        own = db is None
        session = SessionLocal() if own else db
        try:
            async with _library_lock():
                chosen = await self._assign(session, manifest, script, placements)
        finally:
            if own:
                session.close()
        chosen["skipped"] = skipped
        return chosen

    async def _assign(self, db: Session, manifest: dict, script: dict,
                      placements: list[dict]) -> dict:
        language = manifest.get("language") or script.get("language")
        case_id = manifest.get("case_id") or script.get("case_id")
        film_key, bp_id = film_key_for(db, script, manifest)
        self.library.sync_config_cues(db)
        reasons = (_director_reasons(db, bp_id)
                   if any(not p.get("why") for p in placements) else {})
        legacy = _legacy_cue_ids()
        chosen: dict[tuple[str, str], tuple[MusicTrack, str]] = {}
        resolved, generated, cost = [], [], 0
        for p in placements:
            kind = TRACK_KIND.get(p["role"])
            if kind is None:
                continue
            mood = ROOM_TONE_MOOD if kind == "room_tone" else normalize_mood(
                p.get("mood"), p["role"])
            if (kind, mood) in chosen:
                track, _ = chosen[(kind, mood)]
                reason = (f"{track.track_code}: film theme — same {kind}/{mood} track as "
                          "earlier in this film")
            else:
                track, reason = select_track(db, self.library, kind, mood, film_key)
                chosen[(kind, mood)] = (track, reason)
            path, paid = await self.library.ensure_track(db, track)
            if paid is not None:
                generated.append(track.track_code)
                cost += paid
            beat = p.get("after_beat") or (p.get("beats") or [None])[0]
            resolved.append({
                **p, "mood": mood,
                "cue_id": legacy.get((track.kind, track.mood, track.prompt))
                or track.track_code,
                "track_code": track.track_code, "track_id": track.id,
                "path": path, "loop": track.loop, "cue_seconds": track.seconds,
                "why": p.get("why") or reasons.get(beat) or None,
                "selection_reason": reason,
            })
        if case_id is not None:
            record_usages(db, case_id=int(case_id), film_key=film_key,
                          language=language, placements=resolved)
        return {"film_key": film_key, "language": language, "placements": resolved,
                "characters_paid": cost, "tracks_generated": generated}

    async def mix(self, manifest: dict, script: dict, out_dir: Path,
                  db: Session | None = None) -> dict:
        narration = out_dir / "narration.wav"
        chosen = await self.assign_tracks(manifest, script, db)
        resolved = chosen["placements"]

        mix_raw = out_dir / "documentary_unnormalized.wav"
        final_wav = out_dir / "documentary.wav"
        final_mp3 = out_dir / "documentary.mp3"
        if not resolved:
            await asyncio.to_thread(shutil.copyfile, narration, mix_raw)
        else:
            await asyncio.to_thread(_ffmpeg_mix, narration, resolved, mix_raw,
                                    self.loud.sample_rate)
        await asyncio.to_thread(
            A.normalize_loudness, mix_raw, final_wav,
            self.loud.narration_target_lufs, self.loud.true_peak_db,
            self.loud.lra, self.loud.sample_rate, 2,
        )
        after = await asyncio.to_thread(
            A.measure_loudness, final_wav, self.loud.narration_target_lufs,
            self.loud.true_peak_db, self.loud.lra)
        await asyncio.to_thread(A.encode_mp3, final_wav, final_mp3)
        mix_raw.unlink(missing_ok=True)
        music_only = sum(p["duration"] for p in resolved
                         if p["role"] in MUSIC_TYPES)
        return {
            "film_key": chosen["film_key"],
            "placements": [{k: (v if k != "path" else _rel(v)) for k, v in p.items()}
                           for p in resolved],
            "music_characters_paid": chosen["characters_paid"],
            "tracks_generated": chosen["tracks_generated"],
            "selection_reasons": [
                {"beat_id": p.get("after_beat") or (p.get("beats") or [None])[0],
                 "role": p["role"], "track_code": p["track_code"],
                 "why": p.get("why"), "selection_reason": p["selection_reason"]}
                for p in resolved],
            "skipped": chosen["skipped"],
            "music_moments": sum(1 for p in resolved if p["role"] in MUSIC_TYPES),
            "silences": sum(1 for p in resolved if p["role"] == "silence"),
            "beds": sum(1 for p in resolved if p["role"] == "bed"),
            "music_only_seconds": round(music_only, 1),
            "loudness_lufs": after["integrated_lufs"],
            "true_peak_db": after["true_peak_db"],
            "files": {"documentary_wav": _rel(final_wav),
                      "documentary_mp3": _rel(final_mp3)},
        }


# ---------------------------------------------------------------------------
# catalogue (API)
# ---------------------------------------------------------------------------


def _legacy_cue_ids() -> dict[tuple[str, str, str], str]:
    """(kind, mood, prompt) of each config cue → its old cue id."""
    out = {}
    for cue in ai_config.music_library.cues:
        kind, mood = _cue_identity(cue)
        out[(kind, mood, cue.prompt)] = cue.id
    return out


def track_catalogue(db: Session, library: MusicLibrary | None = None) -> list[dict]:
    """Every track with how often, how recently and in which films it was
    used (films in order of first use)."""
    library = library or MusicLibrary()
    library.sync_config_cues(db)
    tracks = (db.query(MusicTrack)
              .order_by(MusicTrack.kind, MusicTrack.mood, MusicTrack.variant).all())
    stats: dict[int, dict] = {}
    for tid, film, first_id, n, last_at in (
            db.query(MusicUsage.track_id, MusicUsage.film_key, func.min(MusicUsage.id),
                     func.count(MusicUsage.id), func.max(MusicUsage.created_at))
            .filter(MusicUsage.track_id.isnot(None))
            .group_by(MusicUsage.track_id, MusicUsage.film_key)
            .order_by(func.min(MusicUsage.id)).all()):
        s = stats.setdefault(tid, {"usage_count": 0, "films": [], "last_used_at": None})
        s["usage_count"] += int(n)
        s["films"].append(film)
        if last_at is not None and (s["last_used_at"] is None or last_at > s["last_used_at"]):
            s["last_used_at"] = last_at
    legacy = _legacy_cue_ids()
    out = []
    for t in tracks:
        s = stats.get(t.id, {"usage_count": 0, "films": [], "last_used_at": None})
        out.append({
            "id": t.track_code, "track_code": t.track_code,
            "cue_id": legacy.get((t.kind, t.mood, t.prompt)),
            "kind": t.kind, "mood": t.mood, "variant": t.variant, "style": t.style,
            "seconds": t.seconds, "loop": t.loop, "prompt": t.prompt,
            "active": t.active, "characters_paid": t.characters_paid,
            "generated": library.track_path(t).exists(),
            "url": f"/api/documentary/music/{t.track_code}/file",
            "usage_count": s["usage_count"], "films_count": len(s["films"]),
            "films": s["films"], "last_used_at": s["last_used_at"],
            "created_at": t.created_at,
        })
    return out


def track_file(db: Session, code: str, library: MusicLibrary | None = None) -> Path | None:
    """File of a track by track_code, or of a config cue by its old id."""
    library = library or MusicLibrary()
    track = db.query(MusicTrack).filter(MusicTrack.track_code == code).first()
    if track is not None:
        return library.track_path(track)
    cue = next((c for c in ai_config.music_library.cues if c.id == code), None)
    return library.path_for(cue) if cue else None


def _tone_shaping(role: str) -> str:
    """Music under words leaves room for the voice: beds lose their top
    end and get a gentle dip in the speech-presence band; room tone is
    darkened so it reads as air, not hiss. Music-only moments are left
    untouched."""
    if role == "bed":
        return "lowpass=f=9000,equalizer=f=2500:t=q:w=1.2:g=-5,"
    if role == "silence":
        return "lowpass=f=5000,"
    return ""


def _ffmpeg_mix(narration: Path, placements: list[dict], dst: Path, sr: int) -> None:
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(narration)]
    for p in placements:
        if p["loop"]:
            args += ["-stream_loop", "-1"]
        args += ["-i", str(p["path"])]
    chains = [f"[0:a]aformat=sample_rates={sr}:channel_layouts=stereo[n]"]
    labels = ["[n]"]
    for i, p in enumerate(placements, 1):
        dur = max(p["duration"], 0.05)
        fi = max(min(p["fade_in"], dur / 2), 0.01)
        fo = max(min(p["fade_out"], dur / 2), 0.01)
        delay = int(round(p["start"] * 1000))
        chains.append(
            f"[{i}:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
            f"atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,{_tone_shaping(p['role'])}"
            f"afade=t=in:st=0:d={fi:.3f},afade=t=out:st={dur - fo:.3f}:d={fo:.3f},"
            f"volume={p['level_db']:.1f}dB,adelay={delay}|{delay}[m{i}]"
        )
        labels.append(f"[m{i}]")
    graph = ";".join(chains) + ";" + "".join(labels) + (
        f"amix=inputs={len(labels)}:normalize=0:duration=first:dropout_transition=0[out]"
    )
    args += ["-filter_complex", graph, "-map", "[out]", "-ac", "2", "-ar", str(sr),
             "-c:a", "pcm_s16le", str(dst)]
    A._run(args)

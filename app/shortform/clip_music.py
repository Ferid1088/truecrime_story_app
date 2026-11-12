"""The music bed of each short clip. Every short gets its own bed, kept in the database (`short_clip_music_tracks`). New beds are made, one per short, until the
library holds `short_clips.video.music.library_target` of them (300); after that no new bed is made and one is taken at random. The same idea in another
language keeps the bed its first clip had. A bed another idea of the case already uses is passed over while the library has others."""

from __future__ import annotations

import json
import random
from math import gcd

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import ShortClip, ShortClipMusicTrack
from app.documentary import storage
from app.documentary.music import MusicLibrary
from app.documentary.music_cost import book_opening

STRIDE = 389          # a prime: every combination is reached, and successive beds differ in most parts of the variation


def variation_of(index: int, variations: dict[str, list[str]]) -> dict[str, str]:
    """The `index`-th sound variation: one entry of each list, the combinations walked in a fixed order that changes most parts from one to the next."""
    names = list(variations)
    sizes = [len(variations[n]) for n in names]
    total = 1
    for size in sizes:
        total *= size
    step = STRIDE
    while gcd(step, total) != 1:
        step += 1
    position = (index * step) % total
    chosen = {}
    for name, size in zip(names, sizes, strict=True):
        position, at = divmod(position, size)
        chosen[name] = variations[name][at]
    return chosen


def variation_key(chosen: dict[str, str]) -> str:
    return "|".join(f"{k}={v}" for k, v in chosen.items())


def prompt_of(base: str, chosen: dict[str, str]) -> str:
    return f"{base} This track: {', '.join(chosen.values())}."


class ClipMusic:
    """Chooses, and when the library is not full makes, the bed of one clip."""

    def __init__(self, db: Session, library: MusicLibrary | None = None, rng: random.Random | None = None):
        self.db = db
        self.library = library or MusicLibrary()
        self.rng = rng or random.Random()
        self.cfg = ai_config.short_clips_settings().video.music

    def active(self) -> list[ShortClipMusicTrack]:
        return [t for t in self.db.query(ShortClipMusicTrack).filter_by(active=True).order_by(ShortClipMusicTrack.id).all()
                if t.file_path and storage.resolve(t.file_path).exists()]

    def _siblings(self, case_id: int) -> list[tuple[ShortClip, int | None]]:
        rows = self.db.query(ShortClip).filter(ShortClip.case_id == case_id).all()
        return [(r, (json.loads(r.media_json or "{}").get("music") or {}).get("track_id")) for r in rows]

    def of_idea(self, case_id: int, clip_key: str) -> ShortClipMusicTrack | None:
        """The bed the same idea already has in another language."""
        for row, track_id in self._siblings(case_id):
            if row.clip_key == clip_key and track_id:
                track = self.db.get(ShortClipMusicTrack, track_id)
                if track is not None and track.active and track.file_path and storage.resolve(track.file_path).exists():
                    return track
        return None

    def used_by_other_ideas(self, case_id: int, clip_key: str) -> set[int]:
        return {track_id for row, track_id in self._siblings(case_id) if track_id and row.clip_key != clip_key}

    async def _make(self, case_id: int) -> ShortClipMusicTrack:
        """One new bed with a sound variation no earlier bed had; paid and booked once."""
        made = self.db.query(ShortClipMusicTrack).count()
        taken = {key for (key,) in self.db.query(ShortClipMusicTrack.variation_key)}
        index = made
        while variation_key(chosen := variation_of(index, self.cfg.variations)) in taken:
            index += 1
        key = variation_key(chosen)
        code = f"clipbed_{index:04d}"
        prompt = prompt_of(self.cfg.prompt, chosen)
        path = self.library.dir / f"{code}.wav"
        paid = await self.library._generate(prompt, self.cfg.generated_seconds, False, path)
        track = ShortClipMusicTrack(track_code=code, variation_key=key, prompt=prompt, seconds=self.cfg.generated_seconds, file_path=storage.rel(path), characters_paid=paid)
        self.db.add(track)
        self.db.commit()
        book_opening(self.db, case_id, f"clip:{code}", self.cfg.generated_seconds)
        return track

    async def choose(self, case_id: int, clip_key: str) -> ShortClipMusicTrack:
        """The same idea keeps its bed; else a new bed while the library is below its target; else a random one that no other idea of the case uses."""
        if (same := self.of_idea(case_id, clip_key)) is not None:
            return same
        available = self.active()
        if self.db.query(ShortClipMusicTrack).count() < self.cfg.library_target or not available:
            return await self._make(case_id)
        used = self.used_by_other_ideas(case_id, clip_key)
        return self.rng.choice([t for t in available if t.id not in used] or available)

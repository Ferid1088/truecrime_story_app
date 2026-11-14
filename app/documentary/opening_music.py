"""The music of each film's opening block. Every case gets `tracks` (three) different dark, mysterious, stressful thriller tracks that all its languages hear, one for each entry of the music, kept in the database (`opening_music_tracks`). A new
track is made for each missing one, with a sound variation no earlier track had, until the library holds `opening_block.music.library_target` of them (60); after that no new
track is made and one is taken at random. A case keeps its tracks: another language, a re-voiced or reviewed opening always gets the same one."""

from __future__ import annotations

import json
import random

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import FilmOpening, OpeningMusicTrack
from app.documentary import storage
from app.documentary.music import MusicLibrary
from app.documentary.music_cost import book_opening
from app.shortform.clip_music import prompt_of, variation_key, variation_of


class OpeningMusic:
    """Chooses, and when the library is not full makes, the track of one case's opening."""

    def __init__(self, db: Session, library: MusicLibrary | None = None, rng: random.Random | None = None):
        self.db = db
        self.library = library or MusicLibrary()
        self.rng = rng or random.Random()
        self.cfg = ai_config.opening_block_settings().music

    def active(self) -> list[OpeningMusicTrack]:
        return [t for t in self.db.query(OpeningMusicTrack).filter_by(active=True).order_by(OpeningMusicTrack.id).all() if t.file_path and storage.resolve(t.file_path).exists()]

    def of_case(self, case_id: int) -> list[OpeningMusicTrack]:
        """The tracks the case's openings already have (in any language), each once, newest rows first."""
        found: list[OpeningMusicTrack] = []
        for row in self.db.query(FilmOpening).filter(FilmOpening.case_id == case_id).order_by(FilmOpening.id.desc()).all():
            for cue in (json.loads(row.media_json or "{}").get("music") or {}).get("tracks") or []:
                track = self.db.get(OpeningMusicTrack, cue.get("track_id"))
                if track is not None and track.active and track.file_path and storage.resolve(track.file_path).exists() and track not in found:
                    found.append(track)
        return found

    async def _make(self, case_id: int) -> OpeningMusicTrack:
        """One new track with a sound variation no earlier track had; paid and booked once."""
        index = self.db.query(OpeningMusicTrack).count()
        taken = {key for (key,) in self.db.query(OpeningMusicTrack.variation_key)}
        while variation_key(chosen := variation_of(index, self.cfg.variations)) in taken:
            index += 1
        code = f"openingbed_{index:04d}"
        prompt = prompt_of(self.cfg.prompt, chosen)
        path = self.library.dir / f"{code}.wav"
        paid = await self.library._generate(prompt, self.cfg.generated_seconds, False, path)
        track = OpeningMusicTrack(track_code=code, variation_key=variation_key(chosen), case_id=case_id, prompt=prompt, seconds=self.cfg.generated_seconds, file_path=storage.rel(path),
                                  characters_paid=paid, use_count=1)
        self.db.add(track)
        self.db.commit()
        book_opening(self.db, case_id, code, self.cfg.generated_seconds)
        return track

    async def choose(self, case_id: int) -> list[OpeningMusicTrack]:
        """The `tracks` different tracks of the case's opening: those its openings already have, then new ones while the library holds fewer usable tracks than its
        target, then random ones the case does not have yet."""
        chosen = self.of_case(case_id)[:self.cfg.tracks]
        while len(chosen) < self.cfg.tracks:
            available = self.active()
            if len(available) < self.cfg.library_target:
                chosen.append(await self._make(case_id))
                continue
            track = self.rng.choice([t for t in available if t not in chosen] or available)
            track.use_count += 1
            self.db.commit()
            chosen.append(track)
        return chosen

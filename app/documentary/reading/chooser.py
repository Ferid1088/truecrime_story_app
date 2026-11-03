"""The word choice of the reading loop: the clip of one word is given to a model with the reading and its most likely wrong forms (one vowel
changed), and it says which one was said. Choosing between two sounds is easier than writing down a sound, and a form that the voice made
does not need to be guessed: it is one of the options."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt_sha256
from app.documentary.reading.ask import Runner, ask
from app.documentary.reading.scheme import SOUND, canonical, split_ezafe

LETTERS = "ABCDEFGHIJ"
MIN_CLIP_BYTES = 800


def variants(reading: str) -> list[str]:
    """The wrong forms of a reading: one vowel changed to a vowel it is mixed up with (the written form of an ezafe is kept)."""
    stem, ezafe = split_ezafe(canonical(reading))
    parts = re.findall(SOUND.pattern + "|.", stem)
    out: list[str] = []
    for i, part in enumerate(parts):
        for other in ai_config.reading.vowel_confusions.get(part, []):
            form = "".join(parts[:i] + [other] + parts[i + 1:]) + (f"-{ezafe}" if ezafe else "")
            if form != reading and form not in out:
                out.append(form)
    return out[: len(LETTERS) - 1]


def clip(wav: Path, start: float, end: float, out: Path) -> None:
    edge = ai_config.reading.clip_edge_s
    begin = max(0.0, start - edge)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{begin:.3f}", "-t", f"{end - begin + edge:.3f}", "-i", str(wav), "-b:a", "64k", str(out)], check=True)
    if not out.exists() or out.stat().st_size < MIN_CLIP_BYTES:
        raise ValueError(f"no sound between {start:.2f} s and {end:.2f} s of {wav.name}")


class WordChooser:
    def __init__(self, db: Session, case_id: int, runner: Runner | None = None):
        self.db, self.case_id, self.runner = db, case_id, runner

    async def choose(self, wav: Path, start: float, end: float, plain: str, reading: str, label: str) -> dict:
        """{"said": the form heard (the reading, a wrong form or "none"), "sure": bool} for the word in [start, end] of the wav (kept with the wav)."""
        forms = [reading] + variants(reading)
        order = sorted(forms, key=lambda f: hashlib.sha256((label + f).encode()).hexdigest())     # the right form is not always first
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "word.mp3"
            await asyncio.to_thread(clip, wav, start, end, mp3)
            data = "data:audio/mpeg;base64," + base64.b64encode(mp3.read_bytes()).decode()
        options = dict(zip(LETTERS, order, strict=False))
        key = hashlib.sha256((data + json.dumps(options, ensure_ascii=False) + prompt_sha256("documentary/reading/choose_system")).encode()).hexdigest()
        saved = wav.with_suffix(".choose.json")
        try:
            kept = json.loads(saved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = {}
        if key in kept:
            return kept[key]
        runner = self.runner or (lambda gen, body: run_agent("documentary.reading.choose", gen, body, images=[data]))
        raw = await ask(self.db, self.case_id, f"Reading: choose {label}", {"word": plain, "options": options}, "reading_listener", runner)
        answer = {"said": options.get(str(raw.get("choice") or "none"), "none"), "sure": bool(raw.get("sure", True))}
        saved.write_text(json.dumps({**kept, key: answer}, ensure_ascii=False), encoding="utf-8")
        return answer

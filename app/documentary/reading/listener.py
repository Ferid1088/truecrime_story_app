"""The blind listener of the reading loop: a model gets the audio of a voiced block and only its plain words, and writes down in Latin letters what
the voice said. It never sees the reading, so it cannot be led by it. What it heard is kept with the audio, so the same audio is never heard twice."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.prompts import prompt_sha256
from app.documentary import audio as A
from app.documentary.reading.ask import Runner, ask
from app.documentary.reading.words import Word


class BlindListener:
    def __init__(self, db: Session, case_id: int, runner: Runner | None = None):
        self.db, self.case_id = db, case_id
        self.runner = runner

    async def hear(self, audio: Path, words: list[Word], label: str) -> dict[int, str]:
        """What the voice said for each word number (a word the listener did not write down is missing)."""
        mp3 = audio.with_suffix(".reading.mp3")
        if not mp3.exists():
            await asyncio.to_thread(A.to_review_mp3, audio, mp3)
        listed = [{"n": w.n, "text": w.plain} for w in words]
        key = hashlib.sha256(mp3.read_bytes() + json.dumps(listed, ensure_ascii=False).encode() + prompt_sha256("documentary/reading/listen_system").encode()).hexdigest()
        saved = audio.with_suffix(".heard.json")
        try:
            kept = json.loads(saved.read_text(encoding="utf-8"))
            if kept.get("key") == key:
                return {int(n): latin for n, latin in kept["heard"].items()}
        except (OSError, ValueError, KeyError):
            pass
        data = "data:audio/mpeg;base64," + base64.b64encode(mp3.read_bytes()).decode()
        runner = self.runner or (lambda gen, body: run_agent("documentary.reading.listen", gen, body, images=[data]))
        raw = await ask(self.db, self.case_id, f"Reading: listen {label}", {"words": listed}, "reading_listener", runner)
        heard = {int(w["n"]): str(w.get("latin") or "") for w in raw.get("words") or [] if isinstance(w, dict) and str(w.get("n", "")).lstrip("-").isdigit()}
        saved.write_text(json.dumps({"key": key, "heard": heard}, ensure_ascii=False), encoding="utf-8")
        return heard

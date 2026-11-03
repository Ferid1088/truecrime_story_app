"""What the Persian pronunciation review page shows and does: the entries of the pronunciation memory by status, where a word is in a narration (with the
times of the words around it, for synchronized playback and highlighting), and the explicit actions of the reviewer. Nothing becomes approved without a
reviewer's action."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import PronunciationEntry, StoryVersion
from app.documentary import voice_review
from app.documentary.pronunciation_memory import PronunciationMemory, nfc
from app.documentary.reading.words import EDGE_PUNCT, key_of

CONTEXT_WORDS = 10                               # words shown before and after the word, with their times


def describe(entry: PronunciationEntry) -> dict:
    origin = json.loads(entry.origin_data or "{}")
    return {"id": entry.id, "language": entry.language, "surface": entry.surface, "surface_key": entry.surface_key, "sense": entry.sense, "scope": entry.scope,
            "prev_word": entry.prev_word, "next_word": entry.next_word, "voice_id": entry.voice_id, "model_id": entry.model_id, "status": entry.status,
            "source": entry.source, "expected_irab": entry.expected_irab, "approved_pronunciation": entry.approved_pronunciation, "approved_ipa": entry.approved_ipa,
            "observed_wrong": entry.observed_wrong, "sentence": entry.sentence, "confidence": entry.confidence, "reviewer": entry.reviewer,
            "approved_at": entry.approved_at.isoformat() if entry.approved_at else None, "version": entry.version, "origin": entry.origin,
            "sources": origin.get("sources", []), "different_readings": origin.get("different_readings", [])}


def entries(db: Session, language: str, status: str | None = None) -> dict:
    query = db.query(PronunciationEntry).filter_by(language=language)
    counts: dict[str, int] = {}
    for e in query.all():
        counts[e.status] = counts.get(e.status, 0) + 1
    rows = (query.filter_by(status=status) if status else query).order_by(PronunciationEntry.surface_key, PronunciationEntry.id).all()
    return {"counts": counts, "entries": [describe(e) for e in rows]}


def narration_words(version: StoryVersion) -> tuple[list[dict], Path | None]:
    """The words of the version's narration with their times in the narration file, and the file."""
    folder = voice_review.folder(version)
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    audio = folder / voice_review.AUDIO["narration"]
    return manifest.get("timeline", {}).get("words", []), audio if audio.exists() else None


def occurrences(db: Session, entry: PronunciationEntry, version: StoryVersion, limit: int = 12) -> dict:
    """Where the word is spoken in the narration. A place where the word is written exactly as the entry has it comes first; a place with another
    written form of the same word (other marks) is shown too, and marked, so the reviewer can still hear it."""
    words, audio = narration_words(version)
    found = []
    for i, w in enumerate(words):
        core = nfc(w["word"].strip(EDGE_PUNCT))
        if key_of(core) != entry.surface_key:
            continue
        around = words[max(0, i - CONTEXT_WORDS): i + CONTEXT_WORDS + 1]
        found.append({"block_id": w.get("block_id"), "form": "exact" if core == entry.surface else "other written form", "written": core,
                      "start": w["start"], "end": w["end"], "hit": i - max(0, i - CONTEXT_WORDS),
                      "words": [{"text": x["word"], "start": x["start"], "end": x["end"]} for x in around]})
    found.sort(key=lambda o: o["form"] != "exact")
    return {"audio_url": f"/api/documentary/versions/{version.id}/audio/narration" if audio else None, "total": len(found), "occurrences": found[:limit]}


def approve(db: Session, entry_id: int, *, pronunciation: str, reviewer: str, scope: str, ipa: str | None, supersede: bool, reason: str) -> dict:
    return describe(PronunciationMemory(db).approve(entry_id, pronunciation=pronunciation, reviewer=reviewer, scope=scope, ipa=ipa, supersede=supersede, reason=reason))


def reject(db: Session, entry_id: int, *, reviewer: str, reason: str) -> dict:
    return describe(PronunciationMemory(db).reject(entry_id, reviewer=reviewer, reason=reason))


def uncertain(db: Session, entry_id: int, *, reviewer: str, reason: str) -> dict:
    return describe(PronunciationMemory(db).mark_uncertain(entry_id, reviewer=reviewer, reason=reason))


def history(db: Session, entry_id: int) -> list[dict]:
    return [{"version": v.version, "actor": v.actor, "reason": v.reason, "at": v.created_at.isoformat(), "state": json.loads(v.snapshot)}
            for v in PronunciationMemory(db).history(entry_id)]


PEAK_RATE = 8000


def peaks(audio: Path, start: float, end: float, count: int = 160) -> list[float]:
    """The loudness of a stretch of audio in `count` steps (0..1), for the waveform of a clip."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.05, end - start):.3f}", "-i", str(audio), "-ac", "1", "-ar",
                          str(PEAK_RATE), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    samples = [int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, len(raw) - 1, 2)]
    if not samples:
        return [0.0] * count
    step = max(1, len(samples) // count)
    top = max(1, max(abs(s) for s in samples))
    return [round(max(abs(s) for s in samples[i:i + step]) / top, 3) for i in range(0, step * count, step)][:count]

"""The listening part of the reading loop: the phoneme recogniser (local, no language-model habit) listens to a voiced block, and the phones of each
word's time window are compared with the word's reading. The words that do not say what the reading says are the findings of the block."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.db.models import StoryVersion
from app.documentary.reading import phones as PH
from app.documentary.reading.words import Word, key_of, tokenize

EDGE = 0.03          # seconds of the neighbours' sound a word's window may take in


@dataclass(frozen=True)
class Finding:
    block_id: str
    n: int
    surface: str
    expected: str
    heard: str


def windows(spoken: list[dict], words: list[Word]) -> dict[int, tuple[float, float]]:
    """The time window of each word of the text: by place when the voice said as many words as the text has, else by the words that match."""
    if len(spoken) == len(words):
        return {w.n: (s["start"], s["end"]) for w, s in zip(words, spoken, strict=True)}
    keys = [key_of(s["word"].strip(EDGE_CHARS)) for s in spoken]
    found: dict[int, tuple[float, float]] = {}
    for tag, a0, a1, b0, _ in SequenceMatcher(a=[w.key for w in words], b=keys, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(a1 - a0):
                found[words[a0 + k].n] = (spoken[b0 + k]["start"], spoken[b0 + k]["end"])
    return found


EDGE_CHARS = ".,;:!?؟،؛«»\"'()[]…-–—"


def inspect_block(block_id: str, text: str, readings: list[str], spoken: list[dict], frames: list[tuple[str, float, float]]) -> list[Finding]:
    """The findings of one block: `frames` are the recogniser's phones with their times, `spoken` the voiced words with theirs."""
    words = tokenize(text)
    if len(readings) != len(words):
        raise ValueError(f"{block_id}: {len(words)} words and {len(readings)} readings")
    found = []
    for n, (start, end) in windows(spoken, words).items():
        if words[n].number:
            continue
        verdict = PH.compare(readings[n], [p for p, s, e in frames if e > start - EDGE and s < end + EDGE])
        if not verdict.ok:
            found.append(Finding(block_id, n, words[n].plain, verdict.expected, verdict.heard))
    return found


async def inspect_version(version: StoryVersion, texts: dict[str, str], readings: dict[str, list[str]], block_ids: list[str] | None = None) -> list[Finding]:
    """The findings of the narration of a version, block by block (the manifest of its voice has the takes and the word times)."""
    from app.documentary.pronunciation import get_listener
    from app.documentary.voice_render import ROOT
    from app.documentary.voice_review import folder

    manifest = json.loads((folder(version) / "manifest.json").read_text(encoding="utf-8"))
    spoken_by_block: dict[str, list[dict]] = {}
    for w in manifest["timeline"]["words"]:
        spoken_by_block.setdefault(w["block_id"], []).append(w)
    listener, found = get_listener(), []
    for block in manifest["timeline"]["blocks"]:
        bid = block["block_id"]
        if bid not in texts or (block_ids is not None and bid not in block_ids):
            continue
        spoken = [{**w, "start": w["start"] - block["start"], "end": w["end"] - block["start"]} for w in spoken_by_block.get(bid, [])]
        frames = await asyncio.to_thread(listener.frames, str(Path(ROOT) / block["wav"]))
        found += inspect_block(bid, texts[bid], readings[bid], spoken, frames)
    return found


def save(version: StoryVersion, findings: list[Finding]) -> Path:
    from app.documentary.voice_review import folder

    path = folder(version) / "reading_findings.json"
    path.write_text(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=1), encoding="utf-8")
    return path

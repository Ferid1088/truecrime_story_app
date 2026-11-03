"""The app verifies a pronunciation itself (switched off: config pronunciation_auto.enabled). For an entry (an imported one, or a candidate) it makes up to three attempts: an independent linguist agent
that is NOT given the entry's reading derives the pronunciation of the word in a sentence of a narration (a different sentence each attempt); if it agrees
the entry is verified; if not, the quality agent judges the entry's reading against that evidence. After the third attempt without agreement the entry is `unresolved`, never
approved. Agreement gives `auto_verified`: below a person's approval. Only linguistic evidence is used here; autonomous use waits for the acoustic evidence."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import PronunciationEntry, StoryVersion
from app.documentary import pronunciation_agents as PA
from app.documentary import pronunciation_review as PR
from app.documentary.irab.rules import canonical
from app.documentary.pronunciation_memory import PronunciationMemory
from app.documentary.reading.words import tokenize

MAX_ATTEMPTS = 3


class AutoDisabled(RuntimeError):
    """Autonomous verification is off: the phonetic recognizer has not passed an independent evaluation yet."""
CONTEXT_WORDS = 8


@dataclass
class Outcome:
    entry_id: int
    surface: str
    attempts: int = 0
    agreed: bool = False
    notes: list[str] = field(default_factory=list)


def sentences_for(db: Session, entry: PronunciationEntry, version: StoryVersion | None) -> list[str]:
    """Sentences of the narration that contain the word (one per place), or the word alone when it is not spoken as written."""
    found = []
    if version is not None:
        for o in PR.occurrences(db, entry, version, limit=MAX_ATTEMPTS)["occurrences"]:
            words = [w["text"] for w in o["words"][max(0, o["hit"] - CONTEXT_WORDS): o["hit"] + CONTEXT_WORDS + 1]]
            found.append(" ".join(words))
    return found or [entry.surface]


def target_index(sentence: str, entry: PronunciationEntry) -> int | None:
    return next((w.n for w in tokenize(sentence) if w.key == entry.surface_key), None)


async def derive(sentence: str, entry: PronunciationEntry, runner) -> tuple[str | None, dict]:
    """What an independent linguist says the word is, without being told what the entry says."""
    words = tokenize(sentence)
    n = target_index(sentence, entry)
    payload = {"sentence": sentence, "context_before": "", "context_after": "", "english": "", "words": [{"n": w.n, "text": w.surface} for w in words], "memory": [],
               "speech_register": "narration"}
    answer = (await runner("linguist", payload)).model_dump()
    return (next((w["irab"] for w in answer["words"] if w["n"] == n), None) if n is not None else None), answer


async def verify(db: Session, entry_id: int, version: StoryVersion | None, runner=PA.run, max_attempts: int = MAX_ATTEMPTS) -> Outcome:
    """Up to three attempts; no agreement leaves the entry unresolved. Nothing a person approved is touched."""
    if not ai_config.pronunciation_auto.enabled:
        raise AutoDisabled("autonomous verification is off until the phonetic recognizer passes an independent evaluation")
    memory = PronunciationMemory(db)
    entry = db.get(PronunciationEntry, entry_id)
    out = Outcome(entry.id, entry.surface)
    if entry.status == "human_approved" or entry.status == "rejected":
        out.notes.append(f"left as it is ({entry.status})")
        return out
    expected = entry.expected_irab
    sentences = sentences_for(db, entry, version)
    for attempt in range(1, max_attempts + 1):
        out.attempts = attempt
        sentence = sentences[(attempt - 1) % len(sentences)]
        try:
            heard, answer = await derive(sentence, entry, runner)
        except Exception as e:  # noqa: BLE001 - a failed model call is a failed attempt
            out.notes.append(f"attempt {attempt}: the linguist failed ({type(e).__name__})")
            continue
        if heard is not None and expected and canonical(heard) == canonical(expected):
            out.agreed = True
            out.notes.append(f"attempt {attempt}: the independent linguist derived the same reading in «{sentence}»")
            break
        verdict = await runner("qa", {"case": {"sentence": sentence, "words": [{"n": w.n, "text": w.surface, "irab": ""} for w in tokenize(sentence)], "reference_irab": expected},
                                      "claims": [{"agent": "memory", "claim": f"{entry.surface} is read {expected}"}],
                                      "evidence": {"independent_linguist": heard, "sources": json.loads(entry.origin_data or "{}").get("sources", [])},
                                      "cost": {"retries": attempt - 1, "characters": 0}})
        qa = verdict.model_dump()
        out.notes.append(f"attempt {attempt}: linguist {heard!r} against {expected!r}; quality agent: {qa['verdict']} ({qa['problem']})")
        if qa["verdict"] == "approve":
            out.agreed = True
            break
    memory.add_evidence(surface=entry.surface, kind="model", verdict="match" if out.agreed else "uncertain", entry_id=entry.id, expected=expected,
                        confidence=0.8 if out.agreed else 0.3, detail="; ".join(out.notes)[:1500])
    memory.auto_verify(entry.id, attempts=out.attempts, agreed=out.agreed, notes=out.notes)
    return out


async def verify_all(db: Session, language: str, version: StoryVersion | None, runner=PA.run, status: str = "legacy_unverified", limit: int | None = None,
                     parallel: int = 6) -> list[Outcome]:
    """Every entry of a status, several at a time (each with a session of its own: the agents' calls are slow)."""
    ids = [e.id for e in db.query(PronunciationEntry).filter_by(language=language, status=status).order_by(PronunciationEntry.id)][:limit]
    version_id = version.id if version is not None else None
    gate = asyncio.Semaphore(parallel)

    async def one(entry_id: int) -> Outcome:
        async with gate:
            with SessionLocal() as own:
                return await verify(own, entry_id, own.get(StoryVersion, version_id) if version_id else None, runner)

    return list(await asyncio.gather(*(one(i) for i in ids)))

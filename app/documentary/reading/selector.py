"""The contextual choice between the readings the lexical evidence allows for a written word. A model that did not write the reading is shown the
sentence, its English source, the word and the lettered candidates (the lexicon's readings and, marked as a guess, the writer's), and chooses by the
word's meaning and grammar in the sentence. Its choice is accepted only when it is a candidate from the lexicon and the model is sure; agreement
with the writer's own guess against the evidence is not evidence, and anything else stays UNCERTAIN."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.prompts import prompt_sha256
from app.documentary.reading import scheme
from app.documentary.reading.ask import Runner, ask
from app.documentary.reading.audit import Finding
from app.documentary.reading.block import BlockInput
from app.documentary.reading.morphology import strong

LETTERS = "ABCDEFGHIJKL"
MAX_CANDIDATES = len(LETTERS) - 1


@dataclass
class Resolution:
    block_id: str
    n: int
    surface: str
    writer: str                       # the writer's reading, as written
    status: str                       # verified | resolved | uncertain
    reading: str                      # what the engine says the reading is (the writer's when it is not resolved)
    reason: str = ""
    candidates: list[str] = field(default_factory=list)


def with_ezafe(candidate: str, writer: str) -> str:
    """The candidate (a reading of the bare word) with the pause and ezafe the writer gave the word in its sentence."""
    body, pause = scheme.split_pause(writer)
    _, ezafe = scheme.split_ezafe(body)
    out = scheme.ezafe_form(candidate) if ezafe and not scheme.split_ezafe(candidate)[1] else candidate
    return out + (scheme.PAUSE if pause else "")


class Selector:
    def __init__(self, db: Session, case_id: int, cache_path: Path, runner: Runner | None = None):
        self.db, self.case_id, self.runner, self.path = db, case_id, runner, cache_path
        try:
            self.cache: dict = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    async def select(self, item: BlockInput, finding: Finding) -> Resolution:
        writer = finding.reading
        pool = finding.options[:MAX_CANDIDATES]
        base = Resolution(item.block_id, finding.n, finding.surface, writer, "uncertain", writer, candidates=pool)
        if not pool:
            return base
        marked = [(c, "lexicon") for c in pool]
        if finding.status != "supported" and all(scheme.canonical(c) != scheme.canonical(writer) for c in pool):
            marked.append((writer, "writer"))
        ordered = sorted(marked, key=lambda m: hashlib.sha256((finding.surface + m[0]).encode()).hexdigest())
        options = {LETTERS[i]: {"reading": reading, "source": source} for i, (reading, source) in enumerate(ordered)}
        payload = {"sentence": " ".join(f"{w.n}:{w.surface}" for w in item.words), "english": item.english, "word": finding.n, "options": options}
        key = hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True) + prompt_sha256("documentary/reading/select_system")).encode()).hexdigest()
        if key not in self.cache:
            runner = self.runner or (lambda gen, body: run_agent("documentary.reading.select", gen, body))
            self.cache[key] = await ask(self.db, self.case_id, f"Reading: select {item.block_id} {finding.n}", payload, "reading_selector", runner)
            self.save()
        answer = self.cache[key]
        chosen = options.get(str(answer.get("choice")))
        reason = str(answer.get("reason") or "")[:160]
        if chosen is None:
            return Resolution(base.block_id, base.n, base.surface, writer, "uncertain", writer, f"the model found no candidate fitting: {reason}", pool)
        if not answer.get("sure", False):
            return Resolution(base.block_id, base.n, base.surface, writer, "uncertain", writer, f"the sentence does not settle it: {reason}", pool)
        if chosen["source"] == "writer":
            return Resolution(base.block_id, base.n, base.surface, writer, "uncertain", writer,
                              f"the model agrees with the writer, the lexicon does not: {reason}", pool)
        same = scheme.canonical(scheme.split_ezafe(scheme.split_pause(writer)[0])[0]) == scheme.canonical(chosen["reading"])
        if same:
            return Resolution(base.block_id, base.n, base.surface, writer, "verified", writer, reason, pool)
        support = set(finding.support.get(chosen["reading"], []))
        if not strong(support):
            return Resolution(base.block_id, base.n, base.surface, writer, "uncertain", writer,
                              f"the sentence favours {chosen['reading']}, but the evidence for it is only {'+'.join(sorted(support)) or 'a single entry'}: {reason}", pool)
        return Resolution(base.block_id, base.n, base.surface, writer, "resolved", with_ezafe(chosen["reading"], writer), reason, pool)

"""The reading text of an Arabic narration (config reading.engines "tashkeel"): every word of a block written with the vowel marks it is said with in its
sentence. The editor writes the block; the words it is unsure of, the names and the ambiguous words are vowelised again by another model that does not see
its answer (the blind check), code compares the two word by word, and an arbiter settles the differences; a word nobody can settle is left as written and
reported. Code validates every answer: the letters of a word never change, only its vowel marks. Nothing is kept from an earlier run unless the caller gives
this run its own cache file. The voice is given the block's text with the vowelised words in place of the plain ones (`TashkeelVoicing`)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt_sha256
from app.documentary.reading.ask import ReadingFailed, ask
from app.documentary.reading.block import BlockInput
from app.documentary.reading.words import EDGE_PUNCT, VOWEL_MARKS, Word, substitute, tokenize
from app.documentary.spoken_numbers.ar import Numbers

log = logging.getLogger(__name__)

PROMPTS = ("write", "check", "arbitrate")
SURE = "high"


@dataclass(frozen=True)
class VocalWord:
    n: int
    text: str                     # the word with its vowel marks (its letters are the block's word, exactly)
    confidence: str = SURE
    ambiguous: bool = False       # the word can be read in more than one way outside its sentence
    name: bool = False
    why: str = ""

    def flagged(self) -> bool:
        return self.ambiguous or self.name or self.confidence != SURE


class ShapeError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:4]))
        self.problems = problems


@dataclass
class TashkeelResult:
    """What the stage found, in the shape the pipeline's reading stage expects (`summary` reads the doubts and the notes)."""

    readings: dict[str, list[VocalWord]]
    doubts: dict[str, set[int]] = field(default_factory=dict)       # block -> the words nobody could settle (left as written)
    notes: dict[str, list[str]] = field(default_factory=dict)
    open_doubts: int = 0                                             # a PPIE language keeps its doubts in the run: the owner is not asked


def canonical(text: str) -> str:
    """The word with each letter's vowel marks in one fixed order (shadda and a vowel are one and the same whichever comes first)."""
    out: list[str] = []
    marks: list[str] = []
    for ch in unicodedata.normalize("NFC", text):
        if VOWEL_MARKS.fullmatch(ch):
            marks.append(ch)
            continue
        out.append("".join(sorted(marks)))
        marks = []
        out.append(ch)
    out.append("".join(sorted(marks)))
    return "".join(out)


def bare(text: str) -> str:
    return VOWEL_MARKS.sub("", unicodedata.normalize("NFC", text))


def sentence_of(item: BlockInput) -> str:
    return " ".join(w.plain + w.after for w in item.words)


def parse(raw: dict, words: list[Word], only: set[int] | None = None) -> dict[int, VocalWord]:
    """The answer, kept to the words asked for: every one once, its letters unchanged (the vowel marks are the whole difference)."""
    asked = {w.n: w for w in words if not w.number and (only is None or w.n in only)}
    found: dict[int, VocalWord] = {}
    problems: list[str] = []
    for entry in raw.get("words") or []:
        if not isinstance(entry, dict) or not str(entry.get("n", "")).isdigit() or int(entry["n"]) not in asked:
            continue
        n = int(entry["n"])
        text = str(entry.get("text") or "").strip().strip(EDGE_PUNCT)
        if n in found:
            problems.append(f"word {n} is answered twice")
        elif bare(text) != asked[n].plain:
            problems.append(f"word {n}: {text!r} does not have the letters of {asked[n].plain!r}")
        else:
            found[n] = VocalWord(n, unicodedata.normalize("NFC", text), str(entry.get("confidence") or SURE).lower(), bool(entry.get("ambiguous")),
                                 bool(entry.get("name")), str(entry.get("why") or ""))
    problems += [f"word {n} has no answer" for n in asked if n not in found and not any(p.startswith(f"word {n}:") for p in problems)]
    if problems:
        raise ShapeError(problems)
    return found


def sha(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def prompts_sha() -> str:
    return sha([prompt_sha256(f"documentary/reading/tashkeel_{name}_system") for name in PROMPTS])


def context(item: BlockInput) -> dict:
    return {"text": sentence_of(item), "previous": item.previous, "next": item.next, "english": item.english}


class TashkeelStage:
    def __init__(self, db, case_id: int, items: list[BlockInput], writer_runner=None, check_runner=None, arbitrate_runner=None, run_cache: Path | None = None):
        self.db, self.case_id, self.items = db, case_id, items
        self.write_runner = writer_runner or (lambda gen, body: run_agent("documentary.reading.tashkeel.write", gen, body))
        self.check_runner = check_runner or (lambda gen, body: run_agent("documentary.reading.tashkeel.check", gen, body))
        self.arbitrate_runner = arbitrate_runner or (lambda gen, body: run_agent("documentary.reading.tashkeel.arbitrate", gen, body))
        self.run_cache = run_cache           # this run's own file: a run stopped by a provider error continues without paying again; no other run reads it
        self.cache: dict = {}
        if run_cache is not None and run_cache.exists():
            self.cache = json.loads(run_cache.read_text(encoding="utf-8"))
        self.stats = {"checked_words": 0, "disagreements": 0, "arbitrated": 0}

    def save(self) -> None:
        if self.run_cache is not None:
            self.run_cache.parent.mkdir(parents=True, exist_ok=True)
            self.run_cache.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    async def write(self, item: BlockInput) -> dict[int, VocalWord]:
        problems: list[str] = []
        for _attempt in range(ai_config.reading.max_writer_retries + 1):
            payload = {**context(item), "words": [{"n": w.n, "text": w.plain} for w in item.words if not w.number],
                       **({"problems": problems} if problems else {})}
            raw = await ask(self.db, self.case_id, f"Tashkeel: write {item.block_id}", payload, "reading_editor", self.write_runner)
            try:
                return parse(raw, item.words)
            except ShapeError as e:
                problems = e.problems
        raise ReadingFailed(f"the vowelisation of {item.block_id} stays wrong after {ai_config.reading.max_writer_retries} retries: {'; '.join(problems[:4])}")

    def to_check(self, item: BlockInput, written: dict[int, VocalWord]) -> set[int]:
        """The words the blind checker vowelises: the ones the editor flagged (ambiguous, a name, not sure), or all of them (config reading.blind_check)."""
        if ai_config.reading.blind_check == "all":
            return set(written)
        return {n for n, r in written.items() if r.flagged()}

    async def check(self, item: BlockInput, written: dict[int, VocalWord], asked: set[int]) -> tuple[dict[int, VocalWord], set[int]]:
        """(the vowelisations after the checker and the arbiter, the words nobody could settle)."""
        problems: list[str] = []
        second: dict[int, VocalWord] = {}
        for _attempt in range(ai_config.reading.max_writer_retries + 1):          # the checker is asked again with the problems, like the editor
            payload = {**context(item), "words": [{"n": n, "text": item.words[n].plain} for n in sorted(asked)], **({"problems": problems} if problems else {})}
            raw = await ask(self.db, self.case_id, f"Tashkeel: check {item.block_id}", payload, "reading_checker", self.check_runner)
            try:
                second = parse(raw, item.words, asked)
                break
            except ShapeError as e:
                problems = e.problems
        else:
            raise ReadingFailed(f"the independent vowelisation of {item.block_id} stays wrong after {ai_config.reading.max_writer_retries} retries: {'; '.join(problems[:4])}")
        self.stats["checked_words"] += len(asked)
        differ = [n for n in sorted(asked) if canonical(second[n].text) != canonical(written[n].text)]
        self.stats["disagreements"] += len(differ)
        if not differ:
            return written, set()
        arbiter = await ask(self.db, self.case_id, f"Tashkeel: arbitrate {item.block_id}",
                            {**context(item), "words": [{"n": n, "word": item.words[n].plain, "a": written[n].text, "b": second[n].text} for n in differ]},
                            "reading_arbiter", self.arbitrate_runner)
        self.stats["arbitrated"] += len(differ)
        decided = {int(e["n"]): e for e in arbiter.get("words") or [] if isinstance(e, dict) and str(e.get("n", "")).isdigit()}
        out, doubts = dict(written), set()
        for n in differ:
            choice, text = str((decided.get(n) or {}).get("choice") or "").lower(), str((decided.get(n) or {}).get("text") or "").strip().strip(EDGE_PUNCT)
            if choice == "b":
                out[n] = VocalWord(n, second[n].text, SURE, written[n].ambiguous, written[n].name, "the arbiter chose the independent reading")
            elif choice == "neither" and bare(text) == item.words[n].plain and text:
                out[n] = VocalWord(n, unicodedata.normalize("NFC", text), SURE, written[n].ambiguous, written[n].name, "the arbiter's own reading")
            elif choice != "a":
                doubts.add(n)
        return out, doubts

    async def block(self, item: BlockInput, gate: asyncio.Semaphore) -> tuple[list[VocalWord], set[int], list[str]]:
        key = sha(context(item), [w.plain for w in item.words], prompts_sha(), ai_config.reading.blind_check)
        saved = self.cache.get(item.block_id)
        if saved and saved["key"] == key:
            return [VocalWord(**r) for r in saved["readings"]], set(saved["doubts"]), saved["notes"]
        async with gate:
            written = await self.write(item)
            asked = self.to_check(item, written)
            settled, doubts, notes = written, set(), []
            if asked:
                settled, doubts = await self.check(item, written, asked)
                notes = [f"word {n}: the checker and the arbiter did not settle it: left as written" for n in sorted(doubts)]
        readings = [settled[w.n] if w.n in settled else VocalWord(w.n, w.surface) for w in item.words]
        self.cache[item.block_id] = {"key": key, "readings": [asdict(r) for r in readings], "doubts": sorted(doubts), "notes": notes}
        self.save()
        return readings, doubts, notes

    async def run(self) -> TashkeelResult:
        gate = asyncio.Semaphore(ai_config.reading.parallel_blocks)
        done = await asyncio.gather(*(self.block(i, gate) for i in self.items))
        result = TashkeelResult({}, {}, {})
        for item, (readings, doubts, notes) in zip(self.items, done, strict=True):
            result.readings[item.block_id], result.notes[item.block_id] = readings, notes
            if doubts:
                result.doubts[item.block_id] = doubts
        return result


class TashkeelVoicing:
    """What the voice is given of the block: its words with their vowel marks, and then every number of that text in the Arabic words a narrator says for it
    (spoken_numbers/ar.py reads each number in its context: the voice reads digits one by one). A word nobody could settle and a block whose words are not
    the words of its reading stay as written."""

    def __init__(self, result: TashkeelResult, vocalize: str | None = None):
        self.result = result
        self.vocalize = vocalize or ai_config.reading.tashkeel_vocalize
        self.numbers = Numbers(ai_config.voice.number_words["ar"], ai_config.reading.tashkeel_number_max)

    def chosen(self, block_id: str, words: list[Word]) -> dict[int, str]:
        readings = self.result.readings.get(block_id)
        if readings is None or len(readings) != len(words):
            log.warning("tashkeel: %s has %d words and %d readings; left as written", block_id, len(words), len(readings or []))
            return {}
        doubted = self.result.doubts.get(block_id, set())
        chosen = {w.n: r.text for w, r in zip(words, readings, strict=True)
                  if not w.number and w.n not in doubted and bare(r.text) == w.plain and (self.vocalize == "all" or r.flagged())}
        return chosen

    def text(self, block_id: str, text: str) -> str:
        return self.numbers.text(substitute(text, self.chosen(block_id, tokenize(text))))      # a number is said as words, never digit by digit

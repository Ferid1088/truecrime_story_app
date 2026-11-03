"""The one transcription of a reading and its check. A reading is valid when every symbol is one of the scheme's (config: reading.transcription).
Two kinds of fault are told apart and never mixed:

- a FORMAT fault: another spelling of a symbol of the scheme (q for gh, x for kh, a curly apostrophe, a capital, a Cyrillic letter that looks
  Latin, a space or a hyphen that only joins the parts of one word). The sound is the same, so the reading is rewritten and every rewrite is recorded;
- a SOUND fault or doubt: a symbol the scheme has not (ē, a lone w) or a reading that is not one word. Its sound cannot be known from its spelling,
  so it is never rewritten: it is reported, and the evidence of the word decides.

A reading that is valid says nothing about whether it is the right word: that is the job of the lexical evidence (evidence.py)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.ai_config import ai_config


def join(text: str) -> str:
    """The parts of one word written together. A hyphen between two letters that would then read as one symbol (k-h, g-h, z-h, s-h, c-h: kh gh zh
    sh ch) is kept: removing it would change the sounds, not only the format."""
    def glue(m: re.Match) -> str:
        before, after = text[m.start() - 1: m.start()], text[m.end(): m.end() + 1]
        return "-" if before in ("k", "g", "z", "s", "c") and after == "h" else ""

    return re.sub(r"[- ]", glue, text)


@dataclass(frozen=True)
class Fix:
    before: str
    after: str


@dataclass(frozen=True)
class Checked:
    reading: str                                # as written
    normalized: str                             # with the format fixes applied (the same sounds)
    fixes: tuple[Fix, ...] = ()
    unknown: tuple[str, ...] = ()               # symbols that are not in the scheme and cannot be rewritten without guessing the sound
    ambiguous: tuple[str, ...] = ()             # symbols whose sound the spelling does not tell
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def status(self) -> str:
        if self.unknown or self.ambiguous or self.problems:
            return "sound_doubt"
        return "format_fixed" if self.fixes else "valid"


class Transcription:
    def __init__(self):
        cfg = ai_config.reading.transcription
        self.cfg = cfg
        symbols = [*cfg.diphthongs, *cfg.consonants, *cfg.vowels, cfg.glottal]
        self.symbol = re.compile("|".join(re.escape(s) for s in sorted(symbols, key=len, reverse=True)))
        self.fixes = sorted(cfg.format_fixes.items(), key=lambda kv: -len(kv[0]))

    def check(self, reading: str) -> Checked:
        text = reading.strip()
        if text == self.cfg.number:
            return Checked(reading, text)
        pause = text.endswith(self.cfg.pause)
        body = text[: -len(self.cfg.pause)].strip() if pause else text
        fixes: list[Fix] = []
        low = body.lower()
        if low != body:
            fixes.append(Fix(body, low))
        body = low
        for before, after in self.fixes:
            if before in body:
                fixes.append(Fix(before, after))
                body = body.replace(before, after)
        stem, dash, tail = body.rpartition("-")
        keep = f"-{tail}" if dash and tail in self.cfg.ezafe else ""
        front = stem if keep else body
        joined = join(front)
        if joined != front:
            fixes.append(Fix("-" if "-" in front else " ", ""))
        body = joined + keep
        problems, unknown, ambiguous = [], [], []
        for part in body.split("-"):
            rest = self.symbol.sub("", part)
            for ch in rest:
                (ambiguous if ch in self.cfg.ambiguous_symbols else unknown).append(ch)
        return Checked(reading, body + (self.cfg.pause if pause else ""), tuple(fixes), tuple(dict.fromkeys(unknown)), tuple(dict.fromkeys(ambiguous)),
                       tuple(problems))

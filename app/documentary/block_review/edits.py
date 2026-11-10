"""What a correction does to the text of a block, and which words it leaves marked. Pure code: no database, no voice."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from app.documentary.words import tokenize


@dataclass(frozen=True)
class WordEdit:
    n: int                       # the word's place in the text the edit is made on
    surface: str | None = None   # the corrected Persian word (None: the word stays as it is)
    finglish: str | None = None  # what the voice is given for the corrected word (an alias); "" clears it


@dataclass(frozen=True)
class Mark:
    n: int                       # the word's place in the new text
    was: str                     # the word as it stood before the first correction
    surface: str                 # the word as corrected
    finglish: str = ""           # what the voice is given for it (an alias)
    pending: bool = True         # not sent to the voice yet


def surfaces(text: str) -> list[str]:
    return [w.surface for w in tokenize(text)]


def apply_words(text: str, edits: list[WordEdit]) -> str:
    """`text` with each corrected word written in its place (the punctuation and tags around it are not touched)."""
    words = tokenize(text)
    out = text
    for e in sorted((e for e in edits if e.surface is not None), key=lambda e: -e.n):
        if not 0 <= e.n < len(words):
            raise ValueError(f"the text has no word {e.n}")
        w = words[e.n]
        out = out[:w.start] + e.surface.strip() + out[w.end:]
    return out


def remap(old: str, new: str, marks: dict[int, Mark]) -> tuple[dict[int, Mark], list[int]]:
    """Where the marks of `old` stand in `new`, and which words of `new` are changed (new or replaced words): a word that is the same stays where it
    moved to, with its mark; a replaced or added word is a correction (it is marked, `was` = the word it replaced when exactly one replaced one)."""
    a, b = surfaces(old), surfaces(new)
    moved: dict[int, Mark] = {}
    changed: list[int] = []
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(b1 - b0):
                if a0 + k in marks:
                    m = marks[a0 + k]
                    moved[b0 + k] = Mark(b0 + k, m.was, m.surface, m.finglish, m.pending)
        elif tag in ("replace", "insert"):
            for k in range(b1 - b0):
                n = b0 + k
                was = a[a0 + k] if tag == "replace" and (a1 - a0) == (b1 - b0) else ""
                before = marks.get(a0 + k) if tag == "replace" and (a1 - a0) == (b1 - b0) else None
                moved[n] = Mark(n, before.was if before else was, b[n])           # a replaced word starts without an alias: the old one was for the old word
                changed.append(n)
    return moved, changed


def find_spans(text: str, find: str) -> list[tuple[int, int]]:
    """Where the words of `find` stand one after the other in `text` (compared without vowel marks and half-spaces, only a space between them): the
    start of the first and the end of the last word of each place."""
    want = [w.key for w in tokenize(find)]
    words = tokenize(text)
    if not want:
        return []
    spans, i = [], 0
    while i + len(want) <= len(words):
        run = words[i:i + len(want)]
        joined = all(not text[a.end:b.start].replace("‌", "").strip() for a, b in zip(run, run[1:], strict=False))
        if [w.key for w in run] == want and joined:
            spans.append((run[0].start, run[-1].end))
            i += len(want)
        else:
            i += 1
    return spans


def replace_text(text: str, find: str, replacement: str) -> tuple[str, int]:
    """`text` with every place of `find` written as `replacement` (the punctuation and tags around stay), and how many places were changed."""
    spans = find_spans(text, find)
    for start, end in reversed(spans):
        text = text[:start] + replacement.strip() + text[end:]
    return text, len(spans)

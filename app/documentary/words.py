"""The words of a block, numbered once by code: the punctuation layer, the block review and the number readers work on the same numbered words."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.documentary.persian_numbers import NUMBER

TAG = re.compile(r"\[[^\[\]]*\]\s*")
PIECE = re.compile(r"\S+")
VOWEL_MARKS = re.compile("[ً-ْٰٔ]")      # fathatan .. sukun, the dagger alef, the hamza above
EDGE_PUNCT = ".,;:!?¿¡؟،؛«»„“”‚‘’\"'()[]…-–—"


@dataclass(frozen=True)
class Word:
    n: int
    surface: str          # as written, with its vowel marks, without the punctuation around it
    plain: str            # the surface without vowel marks
    key: str              # the plain form compared by code: no half-space, one form of ی and ک
    after: str            # the punctuation that follows the word in the text ("" when none)
    number: bool          # digits: read by the number layer
    start: int = -1       # where the word (its surface) stands in the text it was cut from: the text is never rewritten, a change is traced to it
    end: int = -1


def key_of(text: str) -> str:
    return VOWEL_MARKS.sub("", text).replace("‌", "").replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه")


def tokenize(text: str) -> list[Word]:
    """The words of a block in order, each with its place in `text`. Audio tags are not words; a token with no letter or digit is punctuation and joins
    the word before it."""
    words: list[Word] = []
    blanked = TAG.sub(lambda m: " " * len(m.group()), text)          # same length: the offsets are offsets of the text itself
    for m in PIECE.finditer(blanked):
        token = m.group()
        core = token.strip(EDGE_PUNCT)
        if not any(ch.isalnum() for ch in core):
            if words:
                last = words[-1]
                words[-1] = Word(last.n, last.surface, last.plain, last.key, last.after + token, last.number, last.start, last.end)
            continue
        at = m.start() + token.index(core)
        after = token[token.rindex(core) + len(core):] if core else ""
        plain = VOWEL_MARKS.sub("", core)
        words.append(Word(len(words), core, plain, key_of(core), after, bool(NUMBER.fullmatch(core)), at, at + len(core)))
    return words

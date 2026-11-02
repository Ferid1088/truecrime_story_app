"""The words of a block, numbered once by code. Every agent of the reading loop gets the same numbered words and answers by number, so an answer
can never change, drop or merge a word of the text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.documentary.persian_numbers import NUMBER

TAG = re.compile(r"\[[^\[\]]*\]\s*")
VOWEL_MARKS = re.compile("[ً-ْٰٔ]")      # fathatan .. sukun, the dagger alef, the hamza above
EDGE_PUNCT = ".,;:!?؟،؛«»\"'()[]…-–—"
NUMBER_WORD = "NUM"                                          # what a reading says for a number: the digits are read by code, not by an agent


@dataclass(frozen=True)
class Word:
    n: int
    surface: str          # as written, with its vowel marks, without the punctuation around it
    plain: str            # the surface without vowel marks (what a blind listener is given)
    key: str              # the plain form compared by code: no half-space, one form of ی and ک
    after: str            # the punctuation that follows the word in the text ("" when none)
    number: bool          # digits: read by code, never by an agent


def key_of(text: str) -> str:
    return VOWEL_MARKS.sub("", text).replace("‌", "").replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه")


def tokenize(text: str) -> list[Word]:
    """The words of a block in order. Audio tags are not words; a token with no letter or digit is punctuation and joins the word before it."""
    words: list[Word] = []
    for token in TAG.sub(" ", text).split():
        core = token.strip(EDGE_PUNCT)
        if not any(ch.isalnum() for ch in core):
            if words:
                last = words[-1]
                words[-1] = Word(last.n, last.surface, last.plain, last.key, last.after + token, last.number)
            continue
        after = token[token.rindex(core) + len(core):] if core else ""
        plain = VOWEL_MARKS.sub("", core)
        words.append(Word(len(words), core, plain, key_of(core), after, bool(NUMBER.fullmatch(core))))
    return words

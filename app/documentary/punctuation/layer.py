"""The punctuation layer of one block: the text the voice is given (the performance's text: the story's words with its tags and pauses) with
punctuation changed in the gaps between words only. Every change is an Edit at a gap, traced to the word it follows and to that word's place in the
approved text and in the voice's text; `check` refuses a layer whose words, letters, half-spaces or tags differ from the base. The story is never
written: a layer lives for one run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.words import TAG, Word, tokenize


def punctuation(language: str = "fa") -> set[str]:
    """What counts as punctuation in a gap of the language (config semantic_punctuation.languages.<language>.punctuation_chars)."""
    return set(ai_config.semantic_punctuation.profile(language).punctuation_chars)


def is_punctuation(mark: str, language: str = "fa") -> bool:
    """A mark of the language: one or more of its punctuation characters (an ellipsis written as three dots, "?!")."""
    return bool(mark) and set(mark) <= punctuation(language)


@dataclass(frozen=True)
class Edit:
    after: int                  # the word the gap follows (its number in the block)
    old: str                    # the punctuation replaced ("" for an insertion)
    new: str                    # the punctuation put there ("" for a removal)
    reason: str
    source: str = "rule"        # rule | agent

    def describe(self) -> dict:
        return {"after_word": self.after, "old": self.old, "new": self.new, "reason": self.reason, "source": self.source}


class LayerError(ValueError):
    """An edit that would touch something other than punctuation in a gap."""


@dataclass
class Layer:
    block_id: str
    canonical: str               # the block's approved text
    base: str                    # the text the voice would be given without the layer
    edits: list[Edit] = field(default_factory=list)
    story_span: tuple[int, int] | None = None
    language: str = "fa"

    @property
    def words(self) -> list[Word]:
        return tokenize(self.base)

    def gaps(self) -> list[str]:
        """gap[0] before the first word, gap[n + 1] after word n."""
        words, out, at = self.words, [], 0
        for w in words:
            out.append(self.base[at:w.start])
            at = w.end
        out.append(self.base[at:])
        return out

    @property
    def text(self) -> str:
        words, gaps = self.words, self.gaps()
        for e in self.edits:
            gaps[e.after + 1] = applied(gaps[e.after + 1], e, self.language)
        return "".join(gaps[i] + self.base[w.start:w.end] for i, w in enumerate(words)) + gaps[len(words)]

    def word_map(self) -> list[dict]:
        """Each word's place in the approved text, the base and the layer's text (inserted punctuation never shifts a word's reference)."""
        canonical, out_words = tokenize(self.canonical), tokenize(self.text)
        if len(canonical) != len(out_words) or len(self.words) != len(canonical):
            return []                      # the voice text has other words than the approved text (a base that joined two words): no map is made, `check` reports it
        return [{"n": w.n, "word": w.surface, "canonical": [c.start, c.end], "base": [w.start, w.end], "tts": [t.start, t.end],
                 "story": [self.story_span[0] + c.start, self.story_span[0] + c.end] if self.story_span else None}
                for w, c, t in zip(self.words, canonical, out_words, strict=True)]

    def describe(self) -> dict:
        canonical = tokenize(self.canonical)
        return {"block_id": self.block_id, "canonical": self.canonical, "tts_text": self.text, "changed": bool(self.edits),
                "edits": [{**e.describe(), "canonical_offset": canonical[e.after].end if 0 <= e.after < len(canonical) else None}
                          for e in self.edits]}


def applied(gap: str, edit: Edit, language: str = "fa") -> str:
    """The gap with the edit: an insertion right after the word, a replacement or removal of the gap's first such mark (tags untouched)."""
    profile = ai_config.semantic_punctuation.profile(language)
    allowed = set(profile.marks) | set(profile.normalize)
    if edit.new and edit.new not in allowed or edit.old and not is_punctuation(edit.old, language):
        raise LayerError(f"{edit.old!r} -> {edit.new!r} is not a punctuation change the layer may make")
    if not edit.old:
        if any(ch in punctuation(language) for ch in TAG.sub("", gap).strip()[:1]):
            raise LayerError(f"the gap after word {edit.after} already has punctuation: replace it instead")
        return edit.new + gap
    outside = TAG.sub(lambda m: "\0" * len(m.group()), gap)
    at = outside.find(edit.old)
    if at < 0:
        raise LayerError(f"the gap after word {edit.after} has no {edit.old!r}")
    return gap[:at] + edit.new + gap[at + len(edit.old):]


def check(layer: Layer) -> list[str]:
    """What the layer changed that it may not: a word (its letters, marks, half-spaces), the order of the words, a tag, or anything but punctuation
    and the spaces around it. Empty when the layer only changed punctuation."""
    problems = []
    before, after = tokenize(layer.base), tokenize(layer.text)
    if [w.surface for w in before] != [w.surface for w in after]:
        problems.append("the words differ from the base")
    if [w.surface for w in tokenize(layer.canonical)] != [w.surface for w in before]:
        problems.append("the base's words are not the approved text's")
    if TAG.findall(layer.base) != TAG.findall(layer.text):
        problems.append("the tags differ from the base")
    marks = punctuation(layer.language)
    strip = lambda s: re.sub(r"\s", "", "".join(ch for ch in TAG.sub("", s) if ch not in marks))  # noqa: E731
    if strip(layer.base) != strip(layer.text):
        problems.append("something other than punctuation changed")
    return problems

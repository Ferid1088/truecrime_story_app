"""The word lists of the Persian ezafe rules (config ezafe): what holds whatever the context. A function word never takes an ezafe, none is said before
the object marker or a verb, and a text may write the ezafe itself (a kasra, a hamza on a ه, a half-space yeh after a ه)."""

from __future__ import annotations

from app.core.ai_config import ai_config
from app.documentary.words import Word, key_of

KASRA, HAMZA_ABOVE, ZWNJ = "ِ", "ٔ", "‌"


class EzafeRules:
    def __init__(self):
        cfg = ai_config.ezafe
        self.verb_prefixes = tuple(cfg.verb_prefixes)
        self.never_takes = frozenset(key_of(x) for x in cfg.never_takes)
        self.never_before = frozenset(key_of(x) for x in cfg.never_before)


def written_ezafe(word: Word) -> bool:
    """Whether the text writes an ezafe on this word: a kasra at its end, a hamza on its ه (or ۀ), or a half-space yeh after ه."""
    s = word.surface
    return s.endswith(KASRA) or s.endswith("ه" + HAMZA_ABOVE) or s.endswith("ۀ") or s.endswith("ه" + ZWNJ + "ی")


def base_key(word: Word) -> str:
    """The word's key without a written ezafe (فوریه‌ی is فوریه), for the word lists."""
    return key_of(word.surface[:-2] if word.surface.endswith("ه" + ZWNJ + "ی") else word.surface)

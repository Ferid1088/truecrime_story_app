"""Deterministic hard gates for a title candidate (no LLM, no network).

Each gate returns a rejection reason or None. Memorability, curiosity and
native quality are NOT decided here — those are critic opinions that only
rank candidates which passed these gates."""

from __future__ import annotations

import re

from app.core.ai_config import ai_config
from app.identity.titles import clean_title
from app.naming.normalize import norm_title

_ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
_SHOUT = re.compile(r"\b[A-ZÄÖÜ]{4,}\b")


def word_count(title: str) -> int:
    return len(norm_title(title).split())


def brevity_score(title: str) -> float:
    cfg = ai_config.case_naming
    n = word_count(title)
    lo, hi = cfg.preferred_words
    if lo <= n <= hi:
        return 1.0
    if n < lo:
        return 0.4
    return max(0.0, 1.0 - (n - hi) / max(cfg.max_words - hi + 1, 1))


def gate_length(title: str, language: str) -> str | None:
    cfg = ai_config.case_naming
    n = word_count(title)
    if n < 2:
        return "too short (needs at least 2 words)"
    if n > cfg.max_words:
        return f"too long ({n} words, max {cfg.max_words})"
    if len(title) > cfg.max_chars:
        return f"too long ({len(title)} characters, max {cfg.max_chars})"
    return None


def gate_style(title: str, language: str) -> str | None:
    cfg = ai_config.case_naming
    if clean_title(title) != title.strip():
        return "contains an episode number or stray punctuation"
    if _SHOUT.search(title):
        return "ALL CAPS word"
    if "!" in title or title.strip().endswith("…"):
        return "exclamation or trailing ellipsis"
    if cfg.reject_questions and re.search(r"[?؟]\s*$", title.strip()):
        return "question title (false-question clickbait)"
    return None


def gate_generic(title: str, language: str) -> str | None:
    n = norm_title(title)
    for phrase in ai_config.case_naming.generic_phrases.get(language, []):
        p = norm_title(phrase)
        if n == p or (n.startswith(p + " ") and len(n.split()) <= len(p.split()) + 1):
            return f"generic title ('{phrase}')"
    return None


def gate_not_just_the_name(title: str, ctx: dict) -> str | None:
    n = norm_title(title)
    names = [ctx.get("canonical_case_name") or "", *ctx.get("known_public_names", [])]
    for name in names:
        if n and n == norm_title(str(name)):
            return "is the case name, not an editorial title"
    return None


def gate_script(title: str, language: str) -> str | None:
    """Right script for the language (no Latin transliteration of fa/ar)."""
    letters = [c for c in title if c.isalpha()]
    if not letters:
        return "no letters"
    arabic = sum(1 for c in letters if _ARABIC.match(c))
    if language in ("fa", "ar") and arabic / len(letters) < 0.8:
        return f"{language} title must be written in Arabic script"
    if language in ("en", "de") and arabic:
        return f"{language} title contains Arabic script"
    return None


def gate_not_copy_of(title: str, language: str, others: dict[str, list[str]]) -> str | None:
    """A non-English title identical to another language's title is a copy,
    not a native title (proper nouns excepted when the title is just one)."""
    n = norm_title(title)
    for lang, titles in others.items():
        if lang != language and n in {norm_title(t) for t in titles}:
            return f"identical to the {lang} title (not native)"
    return None


def gate_spoiler(title: str, ctx: dict) -> str | None:
    hits = sorted(t for t in norm_title(title).split() if t in set(ctx.get("reveal_terms", [])))
    return f"uses withheld story information ({', '.join(hits)})" if hits else None


def gate_epistemic(title: str, language: str, ctx: dict) -> str | None:
    if ctx.get("claim_limits", {}).get("may_state_guilt"):
        return None
    stems = [norm_title(s) for s in ai_config.case_naming.accusation_terms.get(language, [])]
    for tok in norm_title(title).split():
        forms = {tok}
        if language == "ar":              # definite article / clitics: القاتل, والقاتل
            forms |= {tok[2:], tok[3:]} if tok.startswith(("ال", "لل")) or tok.startswith("وال") else set()
        for stem in stems:
            # German compounds (Kindermörder) carry the stem inside the word
            if stem and any((stem in f if language == "de" and len(stem) >= 5 else f.startswith(stem))
                            for f in forms if f):
                return f"states guilt or killing as fact ('{tok}') but responsibility is not established"
    return None


def run_gates(title: str, language: str, ctx: dict,
              others: dict[str, list[str]] | None = None) -> str | None:
    """First failing gate's reason, else None."""
    for reason in (gate_length(title, language), gate_style(title, language),
                   gate_script(title, language), gate_generic(title, language),
                   gate_not_just_the_name(title, ctx), gate_spoiler(title, ctx),
                   gate_epistemic(title, language, ctx),
                   gate_not_copy_of(title, language, others or {})):
        if reason:
            return reason
    return None

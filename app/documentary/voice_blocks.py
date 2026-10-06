"""Sentence-safe voice blocks for text-to-speech.

Narration is sent to the TTS provider in blocks of roughly 30–90 seconds
(config: ``voice_blocks``) so one block can be re-recorded without
re-recording the whole film. Rules, in priority order:

1. Never cut inside a sentence — a block always ends where a sentence
   ends.
2. Never cut inside a quotation, and keep a speaker's introduction
   ("The police said:") together with the quote that follows.
3. An act boundary always starts a new block (acts later map to beats,
   music and visuals).
4. Prefer ending a block at a paragraph end over ending between two
   sentences of the same paragraph.
5. Balance block sizes around the target and avoid tiny leftover blocks.
6. A single sentence longer than ``max_seconds`` is kept whole and
   flagged ``oversize`` so the writer can split it — this module never
   cuts it.

Sentence detection uses pysbd (rule-based, supports en/de/fa/ar) plus
our own repair rules, because pysbd alone splits Persian/Arabic text
inside «…» quotations, separates a speaker intro ending in ":" from its
quote, and treats Arabic abbreviations such as «د.» (Dr.) as sentence
ends. The repairs only ever MERGE pieces — they never introduce a cut.

Durations are estimates from ``localization.words_per_minute`` until
real audio exists; after TTS, actual audio length replaces them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from functools import lru_cache

import pysbd

from app.core.ai_config import VoiceBlocksConfig, ai_config

# Sentence-final punctuation across en/de/fa/ar (؟ = Arabic-script "?").
_TERMINAL = ".!?…؟"
# Characters that may legitimately follow the terminal mark
# (closing quotes / brackets): 'He said: "Go."' ends after the quote.
_TRAILING_CLOSERS = "\"'»«“”„’‘)]›‹"
# Quotation pairs per language. German closes „ with “, so pairs must be
# language-specific (“ opens in English but closes in German).
_QUOTE_PAIRS: dict[str, list[tuple[str, str]]] = {
    "en": [("“", "”")],
    "de": [("„", "“"), ("»", "«")],
    "fa": [("«", "»"), ("“", "”")],
    "ar": [("«", "»"), ("“", "”")],
}
_DEFAULT_QUOTE_PAIRS = [("«", "»"), ("“", "”")]
# Abbreviations pysbd does not know (Arabic/Persian). Single-letter
# tokens followed by "." are always treated as abbreviations/initials.
_ABBREVIATIONS: dict[str, set[str]] = {
    "ar": {"هـ"},  # Hijri year marker (ha + tatweel)
}
_PYSBD_LANGUAGES = {
    "am", "ar", "bg", "da", "de", "el", "en", "es", "fa", "fr", "hi", "hy",
    "it", "ja", "kk", "mr", "my", "nl", "pl", "ru", "sk", "ur", "zh",
}


# ---------------------------------------------------------------------------
# sentence units
# ---------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _segmenter(language: str) -> pysbd.Segmenter:
    lang = language if language in _PYSBD_LANGUAGES else "en"
    return pysbd.Segmenter(language=lang, clean=False)


def _core(text: str) -> str:
    """Text without trailing whitespace and closing quotes/brackets."""
    return text.rstrip().rstrip(_TRAILING_CLOSERS).rstrip()


def _ends_sentence(text: str) -> bool:
    core = _core(text)
    return bool(core) and core[-1] in _TERMINAL


def _quote_open(text: str, language: str) -> bool:
    if text.count('"') % 2:
        return True
    for opener, closer in _QUOTE_PAIRS.get(language, _DEFAULT_QUOTE_PAIRS):
        if text.count(opener) != text.count(closer):
            return True
    return False


def _ends_with_abbreviation(text: str, language: str) -> bool:
    core = _core(text)
    if not core.endswith("."):
        return False
    tokens = core[:-1].split()
    if not tokens:
        return False
    last = tokens[-1].lstrip("(«„“\"'")
    return len(last) == 1 or last in _ABBREVIATIONS.get(language, set())


def _needs_more(text: str, language: str) -> bool:
    """True when ending a unit here would cut a sentence."""
    stripped = text.rstrip()
    return (
        _quote_open(text, language)
        or stripped.endswith((":", "："))
        or not _ends_sentence(text)
        or _ends_with_abbreviation(text, language)
    )


def split_sentences(paragraph: str, language: str) -> list[str]:
    """Split one paragraph into sentence units that are safe to end a
    voice block after. Whitespace inside the paragraph is normalized;
    no other character is changed, added or dropped."""
    text = " ".join((paragraph or "").split())
    if not text:
        return []
    pieces = _segmenter(language).segment(text)
    if "".join(pieces) != text:
        # Never risk losing or altering narration: keep it whole.
        return [text]
    units: list[str] = []
    buf = ""
    for piece in pieces:
        buf += piece
        if _needs_more(buf, language):
            continue
        units.append(buf.strip())
        buf = ""
    if buf.strip():
        units.append(buf.strip())  # paragraph end always closes a unit
    return units


# ---------------------------------------------------------------------------
# packing
# ---------------------------------------------------------------------------


@dataclass
class _Unit:
    text: str
    words: int
    seconds: float
    paragraph_end: bool


@dataclass
class VoiceBlock:
    block_id: str
    index: int
    section_id: str
    section_block_index: int
    text: str
    sentence_count: int
    word_count: int
    est_seconds: float
    ends_paragraph: bool
    oversize: bool
    content_hash: str
    previous_text: str = ""
    next_text: str = ""


def _pack(units: list[_Unit], cfg: VoiceBlocksConfig) -> list[tuple[int, int]]:
    """Optimal split of one act's sentence units into blocks.

    Dynamic programming over block end positions: each block costs its
    squared relative deviation from target_seconds, plus a penalty when
    it is shorter than min_seconds (unless the whole act is) and a
    penalty when it ends between two sentences of the same paragraph.
    Blocks longer than max_seconds are impossible unless they consist of
    one single (oversize) sentence.
    """
    n = len(units)
    if n == 0:
        return []
    total = sum(u.seconds for u in units)
    best = [float("inf")] * (n + 1)
    prev = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        dur = 0.0
        for i in range(j - 1, -1, -1):
            dur += units[i].seconds
            if dur > cfg.max_seconds and j - i > 1:
                break
            cost = ((dur - cfg.target_seconds) / cfg.target_seconds) ** 2
            if dur < cfg.min_seconds and total >= cfg.min_seconds:
                cost += cfg.short_block_penalty
            if j < n and not units[j - 1].paragraph_end:
                cost += cfg.sentence_break_penalty
            if best[i] + cost < best[j]:
                best[j] = best[i] + cost
                prev[j] = i
    spans = []
    j = n
    while j > 0:
        spans.append((prev[j], j))
        j = prev[j]
    return spans[::-1]


def _join_units(units: list[_Unit]) -> str:
    out = ""
    for k, u in enumerate(units):
        if k:
            out += "\n\n" if units[k - 1].paragraph_end else " "
        out += u.text
    return out


def _tail(units: list[_Unit], limit: int) -> str:
    """Last whole sentences of a block, up to `limit` characters."""
    if limit <= 0 or not units:
        return ""
    picked: list[str] = []
    size = 0
    for u in reversed(units):
        if picked and size + len(u.text) + 1 > limit:
            break
        picked.insert(0, u.text)
        size += len(u.text) + 1
    text = " ".join(picked)
    return text if len(text) <= limit else text[-limit:].split(" ", 1)[-1]


def _head(units: list[_Unit], limit: int) -> str:
    """First whole sentences of a block, up to `limit` characters."""
    if limit <= 0 or not units:
        return ""
    picked: list[str] = []
    size = 0
    for u in units:
        if picked and size + len(u.text) + 1 > limit:
            break
        picked.append(u.text)
        size += len(u.text) + 1
    text = " ".join(picked)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0]


def _safe_id(section_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", section_id or "full")[:40] or "full"


def plan_voice_blocks(
    sections: list[dict],
    language: str,
    cfg: VoiceBlocksConfig | None = None,
    words_per_minute: int | None = None,
    split_language: str | None = None,
) -> dict:
    """Plan TTS blocks for narration given as ordered act sections
    ([{"id", "text"}]). Pure function: no provider calls, no DB.
    `split_language`: sentence rules to use when the script differs from
    the language (Finglish = Persian in Latin letters -> "en")."""
    cfg = cfg or ai_config.voice_blocks
    wpm = words_per_minute or ai_config.words_per_minute_for(language)
    per_block: list[tuple[dict, list[_Unit]]] = []

    for section in sections:
        units: list[_Unit] = []
        paragraphs = [
            p for p in re.split(r"\n\s*\n", section.get("text") or "") if p.strip()
        ]
        for para in paragraphs:
            sentences = split_sentences(para, split_language or language)
            for k, s in enumerate(sentences):
                words = len(s.split())
                units.append(
                    _Unit(s, words, words / wpm * 60.0, k == len(sentences) - 1)
                )
        for n, (i, j) in enumerate(_pack(units, cfg), 1):
            chunk = units[i:j]
            per_block.append((
                {
                    "section_id": section.get("id") or "full",
                    "section_block_index": n,
                },
                chunk,
            ))

    blocks: list[VoiceBlock] = []
    for idx, (meta, chunk) in enumerate(per_block):
        text = _join_units(chunk)
        seconds = sum(u.seconds for u in chunk)
        blocks.append(
            VoiceBlock(
                block_id=(
                    f"{language.upper()}_{_safe_id(meta['section_id'])}_"
                    f"{meta['section_block_index']:02d}"
                ),
                index=idx,
                section_id=meta["section_id"],
                section_block_index=meta["section_block_index"],
                text=text,
                sentence_count=len(chunk),
                word_count=sum(u.words for u in chunk),
                est_seconds=round(seconds, 1),
                ends_paragraph=chunk[-1].paragraph_end,
                oversize=len(chunk) == 1 and seconds > cfg.max_seconds,
                content_hash=hashlib.sha256(
                    f"{language}\n{text}".encode("utf-8")
                ).hexdigest()[:16],
                previous_text=(
                    _tail(per_block[idx - 1][1], cfg.context_chars) if idx else ""
                ),
                next_text=(
                    _head(per_block[idx + 1][1], cfg.context_chars)
                    if idx + 1 < len(per_block) else ""
                ),
            )
        )

    return {
        "language": language,
        "words_per_minute": wpm,
        "settings": cfg.model_dump(),
        "block_count": len(blocks),
        "total_est_seconds": round(sum(b.est_seconds for b in blocks), 1),
        "oversize_block_ids": [b.block_id for b in blocks if b.oversize],
        "blocks": [asdict(b) for b in blocks],
    }


def plan_for_version(version) -> dict:
    """Voice-block plan for a stored StoryVersion. Uses the saved act
    sections when they add up to the approved text; otherwise the whole
    story is one section (legacy versions)."""
    from app.agents.story import is_structured, stored_sections

    sections = stored_sections(version) or [
        {"id": "full", "text": version.story_text or ""}
    ]
    plan = plan_voice_blocks(sections, version.language or "en")
    plan["story_version_id"] = version.id
    plan["structured"] = is_structured(sections)
    return plan

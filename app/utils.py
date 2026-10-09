import re
import hashlib
from datetime import datetime, timezone


def new_case_uid() -> str:
    """Stable technical identity of a case (never shown to the audience)."""
    import secrets
    return "CASE_" + secrets.token_hex(3)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Normalise a possibly-naive datetime to aware UTC.

    Rows written before the tz-aware refactor read back naive on SQLite;
    they are treated as UTC rather than local time.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_ARABIC_SCRIPT = re.compile(r"[؀-ۿ]")
_LATIN_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}")

# ---------------------------------------------------------------------------
# Deterministic source-language detection (research hardening Part 3).
# Never trust model-declared language blindly — measure the text.
# ---------------------------------------------------------------------------

_ARABIC_CHAR = re.compile(r"[؀-ۿݐ-ݿࠠ-ࣿﭐ-﷿ﹰ-﻿]")
# Letters that occur in Persian but never in Arabic.
_FA_LETTERS = set("پچژگٱ")
_FA_STOP = {
    "و", "در", "که", "از", "به", "با", "این", "است", "برای", "آن", "را",
    "بر", "یک", "شد", "نیز", "خود", "او", "گفت", "بود", "می", "های",
}
_AR_STOP = {
    "في", "على", "إلى", "أن", "الذي", "التي", "عن", "من", "هذا", "هذه",
    "قال", "كان", "بعد", "عند", "كما", "لم", "لكن", "بين", "أم", "يا",
}
_EN_STOP = {
    "the", "of", "and", "in", "to", "was", "for", "on", "with", "is",
    "at", "by", "an", "be", "this", "had", "from", "that", "were",
}
_DE_STOP = {
    "der", "die", "das", "und", "in", "den", "von", "zu", "mit", "auf",
    "ist", "im", "für", "dem", "ein", "eine", "als", "auch", "nicht",
    "wurde", "bei", "nach", "dass", "werden", "sie", "es", "aus",
}
_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+|[؀-ۿݐ-ࣿ]+")


def detect_language(text: str, min_chars: int = 80) -> tuple[str | None, float]:
    """Detect en/de/fa/ar from text.

    Returns (language_code | None, confidence 0..1). Returns (None, 0.0)
    when there is too little signal. Script is decisive for fa/ar;
    German vs English is decided by stopword frequency + diacritics.
    """
    if not text:
        return None, 0.0
    sample = text[:5000]
    words = [w.lower() for w in _WORD.findall(sample)]
    if not words:
        return None, 0.0
    arabic_words = [w for w in words if _ARABIC_CHAR.search(w)]
    latin_words = [w for w in words if re.match(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+$", w)]
    total = len(words)
    if len(sample) < min_chars or total < 15:
        return None, 0.0

    if len(arabic_words) / total > 0.15:
        fa_marks = sum(1 for w in arabic_words if set(w) & _FA_LETTERS)
        fa_score = (fa_marks / len(arabic_words)) + sum(
            1 for w in arabic_words if w in _FA_STOP
        ) / len(arabic_words)
        ar_score = sum(
            1 for w in arabic_words if w in _AR_STOP
        ) / len(arabic_words)
        if fa_score >= ar_score:
            return "fa", min(0.99, 0.55 + fa_score)
        return "ar", min(0.99, 0.55 + ar_score)

    if not latin_words or len(latin_words) / total < 0.3:
        return None, 0.0
    lw = latin_words
    de_hits = sum(1 for w in lw if w in _DE_STOP)
    en_hits = sum(1 for w in lw if w in _EN_STOP)
    diacritics = sum(1 for w in lw if re.search(r"[äöüß]", w))
    de_score = (de_hits + 2 * diacritics) / len(lw)
    en_score = en_hits / len(lw)
    if de_score == 0 and en_score == 0:
        return None, 0.0
    lang = "de" if de_score > en_score else "en"
    conf = min(
        0.98, 0.5 + abs(de_score - en_score) / max(de_score + en_score, 0.01)
    )
    return lang, conf


def language_quality(text: str, language: str, cfg) -> dict:
    """Measure script integrity for non-Latin target languages.

    Returns {"script_ratio": float, "foreign_token_ratio": float, "pass": bool}.
    `cfg` is an ai_config LanguageQualityConfig (or None -> pass).
    Proper names/acronyms in Latin script are unavoidable and only
    count toward the foreign-token ratio, not the script ratio.
    """
    if cfg is None:
        return {"script_ratio": 1.0, "foreign_token_ratio": 0.0, "pass": True}
    chars = [c for c in text if c.isalpha()]
    target = _ARABIC_SCRIPT.findall(text)
    script_ratio = (len(target) / len(chars)) if chars else 0.0
    tokens = text.split()
    foreign = _LATIN_WORD.findall(text)
    foreign_ratio = (len(foreign) / len(tokens)) if tokens else 0.0
    return {
        "script_ratio": round(script_ratio, 4),
        "foreign_token_ratio": round(foreign_ratio, 4),
        "pass": (
            script_ratio >= cfg.minimum_target_script_ratio
            and foreign_ratio <= cfg.max_foreign_token_ratio
        ),
    }


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE).strip()
    cleaned = re.sub(r"[-\s]+", "-", cleaned)
    return cleaned[:450] or hashlib.sha1(text.encode()).hexdigest()[:16]


def fingerprint(text: str) -> str:
    normalized = re.sub(r"\W+", " ", text.lower()).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    """Stable hash of a story text — used to prove a critic score belongs
    to the exact text stored on a StoryVersion."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


_HEADING_LINE = re.compile(r"^\s*#{1,6}\s+\S")
_SEPARATOR_LINE = re.compile(r"^\s*([-*_])\1{2,}\s*$")
# Presentation markdown emphasis — strip the markers, keep the words.
# Bold must run before italic so `**x**` is consumed first.
_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_ITALIC = re.compile(r"(?<![\*\w])\*(\S[^*\n]*?\S|\S)\*(?![\*\w])")
_ACT_LABEL_LINE = re.compile(
    r"^\s*[\[\(\{]*\s*(act|chapter|acte|پرده|فصل|بخش)\s+"
    r"(یک|اول|دوم|سوم|چهارم|پنجم|ششم|هفتم|هشتم|نهم|دهم|\d+|[0-9۰-۹]+)"
    r"[\s\w۰-۹:.\-\[\]\(\)]{0,60}$",
    re.IGNORECASE,
)


def strip_narration_artifacts(text: str) -> str:
    """Remove markdown headings, separator rules and ACT/CHAPTER label
    lines from generated narration while preserving paragraph structure."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if _HEADING_LINE.match(line) or _SEPARATOR_LINE.match(line):
            continue
        if _ACT_LABEL_LINE.match(stripped):
            continue
        kept.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    cleaned = _MD_BOLD.sub(r"\1", cleaned)
    cleaned = _MD_ITALIC.sub(r"\1", cleaned)
    return cleaned

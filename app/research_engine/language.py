"""Source language detection (Part 17).

The query language is never trusted. Detection cascades:
fetched text -> title+snippet -> page metadata -> (caller may add a
model fallback). Returns measured language + confidence + method.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.utils import detect_language as _detect

_META_MAP = {
    "de": "de", "de-de": "de", "de-at": "de", "de-ch": "de",
    "en": "en", "en-us": "en", "en-gb": "en", "en-au": "en",
    "fa": "fa", "fa-ir": "fa", "per": "fa", "pes": "fa",
    "ar": "ar", "ar-sa": "ar", "ar-eg": "ar", "ara": "ar",
}


@dataclass
class LanguageResult:
    language: str | None
    confidence: float
    method: str          # fetched_text | snippet | page_metadata | unknown
    declared: str | None = None   # what the query/publisher claimed


def detect_source_language(
    fetched_text: str | None,
    title: str | None = None,
    snippet: str | None = None,
    html_lang: str | None = None,
    query_language: str | None = None,
) -> LanguageResult:
    declared = query_language
    if fetched_text and len(fetched_text.strip()) >= 80:
        lang, conf = _detect(fetched_text)
        if lang:
            return LanguageResult(lang, conf, "fetched_text", declared)
    probe = " ".join(t for t in (title, snippet) if t)
    if len(probe.strip()) >= 30:
        lang, conf = _detect(probe)
        if lang:
            return LanguageResult(lang, conf * 0.85, "snippet", declared)
    if html_lang:
        lang = _META_MAP.get(html_lang.strip().lower())
        if lang:
            return LanguageResult(lang, 0.6, "page_metadata", declared)
    return LanguageResult(None, 0.0, "unknown", declared)

"""Deterministic source-quality classification (Part 16).

Categorical provenance first, numeric score second — the score never
erases the category. An optional LLM pass can refine `unknown` rows,
but every classification keeps its deterministic base.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# Domain patterns -> (source_type, base_score). Checked in order.
_RULES: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"(^|\.)gov(\.|$)|(^|\.)gov\.[a-z]{2}$|(^|\.)gc\.ca$|"
                r"(^|\.)bund\.de$|(^|\.)govt\.[a-z]{2}$", re.I),
     "government_archive", 0.95),
    (re.compile(r"court|justice\.|coroner|coronial", re.I),
     "court_record", 0.95),
    (re.compile(r"police|polizei|sheriff|fbi\.gov|interpol", re.I),
     "police_record", 0.95),
    (re.compile(r"(^|\.)edu(\.|$)|(^|\.)ac\.[a-z]{2}|jstor|springer|"
                r"sciencedirect|ncbi|pubmed", re.I),
     "academic", 0.9),
    (re.compile(r"abc\.net\.au|bbc\.|nytimes|theguardian|washingtonpost|"
                r"reuters|ap\.org|apnews|bbc\.co|dw\.com|spiegel|zeit\.de|"
                r"faz\.net|sueddeutsche|smh\.com\.au|theage\.com\.au|"
                r"thewest\.com\.au|perthnow|rnz\.co\.nz|cbc\.ca|npr\.org|"
                r"bbc\.persian|irinn\.ir|iranintl|voanews|rferl", re.I),
     "credible_journalism", 0.85),
    (re.compile(r"archive\.org|trove\.nla\.gov\.au|newspapers\.com|"
                r"paperspast|britishnewspaperarchive", re.I),
     "government_archive", 0.8),
    (re.compile(r"youtube\.com|youtu\.be|vimeo", re.I),
     "documentary", 0.6),
    (re.compile(r"spotify\.com|podcasts\.apple|podbean|libsyn|acast|"
                r"podcast", re.I),
     "documentary", 0.6),
    (re.compile(r"wikipedia\.org", re.I), "aggregator", 0.45),
    (re.compile(r"reddit\.com|quora\.com|forums?\.|websleuths", re.I),
     "forum", 0.25),
    (re.compile(r"facebook\.com|instagram|tiktok|twitter\.com|x\.com", re.I),
     "forum", 0.2),
    (re.compile(r"fandom\.com|famousdeaths|murderpedia|allthatsinteresting|"
                r"ranker|listverse|medium\.com|substack|blogspot|wordpress",
                re.I),
     "creator_narration", 0.35),
    (re.compile(r"mamamia|dailymail|thesun|mirror\.co|nypost|buzzfeed", re.I),
     "aggregator", 0.4),
]

_CONTENT_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"coroners? (court|report|finding)", re.I), "coroner_record"),
    (re.compile(r"police (statement|report|media release)", re.I), "police_record"),
    (re.compile(r"court (document|filing|transcript)", re.I), "court_record"),
    (re.compile(r"documentary|docuseries|investigative podcast", re.I), "documentary"),
    (re.compile(r"interview with|exclusive interview", re.I), "primary_interview"),
]

# Deterministic type -> project Source.source_type mapping.
TYPE_TO_SOURCE_TYPE = {
    "primary_official": "official", "court_record": "official",
    "police_record": "official", "coroner_record": "official",
    "government_archive": "archive", "primary_interview": "news",
    "academic": "archive", "credible_journalism": "news",
    "local_journalism": "news", "documentary": "video",
    "expert_analysis": "analysis", "creator_narration": "creator",
    "aggregator": "wiki", "forum": "forum", "unknown": "other",
}


@dataclass
class SourceQuality:
    source_type: str          # categorical provenance (Part 16 list)
    quality_score: float      # 0..1, derived, never replaces the type
    signals: list[str]


def classify_source(url: str, title: str | None = None,
                    snippet: str | None = None,
                    text_sample: str | None = None) -> SourceQuality:
    host = ""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        pass
    signals: list[str] = []
    stype = "unknown"
    score = 0.5
    for pattern, t, s in _RULES:
        if host and pattern.search(host):
            stype, score = t, s
            signals.append(f"domain:{t}")
            break
    probe = " ".join(x for x in (title, snippet, (text_sample or "")[:1500]) if x)
    for pattern, t in _CONTENT_HINTS:
        if stype in ("unknown", "credible_journalism", "local_journalism",
                     "aggregator") and pattern.search(probe):
            stype = t
            score = max(score, 0.85)
            signals.append(f"content:{t}")
            break
    if stype == "unknown":
        # Unknown domain with real content: treat as local journalism
        # floor rather than "forum" — news sites vastly outnumber forums.
        stype = "local_journalism" if text_sample else "unknown"
        score = 0.55 if text_sample else 0.3
    return SourceQuality(stype, round(score, 3), signals)

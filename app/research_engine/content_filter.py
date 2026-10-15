"""Deterministic source-safety and case-relevance checks before fetching."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from app.research_engine.search import SearchResult

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)
_CONFIG = Path(__file__).resolve().parents[2] / "config" / "research_safety.json"


@lru_cache(maxsize=1)
def config() -> dict:
    return json.loads(_CONFIG.read_text(encoding="utf-8"))


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if len(w) > 2}


def case_terms(case_context: dict) -> set[str]:
    parts = [
        case_context.get("title") or "",
        case_context.get("summary") or "",
        case_context.get("location") or "",
        " ".join(case_context.get("aliases") or []),
        " ".join(case_context.get("key_people") or []),
    ]
    return _tokens(" ".join(parts))


def inspect_result(result: SearchResult, case_context: dict, query: str | None = None,
                   language: str | None = None) -> tuple[str | None, float]:
    """Return (reason, relevance), where reason means do not store/fetch.

    The case terms are written in the case's language. For a search in another
    language they share no token with a page in another script, so the page is
    judged against the terms of the query that found it instead.
    """
    cfg = config()
    host = (urlsplit(result.url or "").hostname or "").lower().removeprefix("www.")
    for domain in cfg.get("blocked_domains", []):
        domain = domain.lower().removeprefix("www.")
        if host == domain or host.endswith("." + domain):
            label = "adult content" if domain in {"tabootube.xxx", "taboodude.com", "pornhub.com", "xvideos.com", "xnxx.com"} else "junk"
            return f"blocked_{label}", 0.0
    haystack = f"{result.url} {result.title or ''} {result.snippet or ''}".lower()
    for label, keywords in cfg.get("blocked_keywords", {}).items():
        if any(keyword.lower() in haystack for keyword in keywords):
            return f"blocked_{label}", 0.0
    case_language = (case_context.get("language") or "en")[:2].lower()
    if query and language and language[:2].lower() != case_language:
        terms = _tokens(query)
    else:
        terms = case_terms(case_context)
    candidate = _tokens(f"{result.title or ''} {result.snippet or ''}")
    relevance = len(terms & candidate) / max(1, len(terms))
    # A one-token context (used by intentionally minimal engine fixtures and
    # by a user who has supplied only a placeholder) cannot support a
    # meaningful lexical verdict. The run-level preflight asks the user for
    # better identity details; once two or more identity terms exist, weak
    # results are dropped before fetch/storage.
    if len(terms) >= 2 and relevance < float(cfg.get("min_relevance_score", 0.12)):
        return "irrelevant", round(relevance, 4)
    return None, round(relevance, 4)


def minimum_relevant_sources() -> int:
    return max(1, int(config().get("min_relevant_sources", 1)))

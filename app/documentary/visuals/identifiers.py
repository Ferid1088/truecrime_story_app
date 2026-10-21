"""Real identifiers for an entity dossier (P1): fetched from the official APIs, never guessed
or invented by a model. Wikidata's own search (wbsearchentities) and a Commons category search
(reusing the match rule research.py already uses for its own category harvesting)."""

from __future__ import annotations

import httpx

from app.documentary.visuals.research import category_matches

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def _headers() -> dict[str, str]:
    # Wikidata/Commons robot policy requires an identifying User-Agent (a bare request is
    # refused with 403 "Please set a user-agent..."); reuse the one already configured for
    # Wikimedia requests elsewhere (visual_search.user_agent).
    from app.core.ai_config import ai_config

    return {"User-Agent": ai_config.visual_search.user_agent}


async def wikidata_id(client: httpx.AsyncClient, name: str) -> str | None:
    """The QID of Wikidata's own top search hit for `name`, or None. This is the search API's
    own ranking, not a confirmed identity match — recorded as a best-effort identifier."""
    try:
        r = await client.get(WIKIDATA_API, headers=_headers(), params={
            "action": "wbsearchentities", "search": name, "language": "en",
            "format": "json", "limit": 1})
        r.raise_for_status()
        hits = r.json().get("search") or []
        return str(hits[0]["id"]) if hits else None
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


async def commons_category(client: httpx.AsyncClient, name: str) -> str | None:
    """The Commons category that names every word of `name`, or None (the same match rule
    research.py's own category harvesting uses)."""
    try:
        r = await client.get(COMMONS_API, headers=_headers(), params={
            "action": "query", "list": "search", "srsearch": name,
            "srnamespace": 14, "srlimit": 1, "format": "json"})
        r.raise_for_status()
        hits = (r.json().get("query") or {}).get("search") or []
        if hits and category_matches(name, hits[0]["title"]):
            return str(hits[0]["title"])
        return None
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None

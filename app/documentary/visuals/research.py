"""Visual research (Part 21): finds real photos and documents for the
case's visual needs, downloads them safely and adds them to the library
as UNVERIFIED assets with provider metadata and a rights status.

Providers (config visual_search.providers):
  source_pages   — images embedded in the case's own sources (news
                   articles, police/coroner pages): og:image, figures
                   with captions. Most case-specific, editorial review.
  wikimedia      — Wikimedia Commons file search with license metadata
                   (places, buildings, landscapes, public figures).

Search is by semantic need (entities from the visual requirement
planner), never sentence by sentence. Nothing is trusted yet: the
verification agent decides what an image actually shows.

Media library (Master task §8): every asset records what it was found
for (entity key and type, the query, research or production-time search,
the date) and a provisional relevance tier (visuals/tiers.py). Exact
queries name the case's own people and places; CONTEXT queries look for
contextually accurate imagery (the region, the kind of place, the
period) and therefore only ask providers whose rights are known — never
case source pages or open web image search. Footage (visuals/footage.py)
is searched for the queries flagged `footage` (else the top entities).
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, Source, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    url: str
    provider: str
    page_url: str | None = None
    title: str | None = None
    caption: str | None = None
    license: str | None = None
    credit: str | None = None
    source_name: str | None = None
    found_for: str | None = None
    entities: list[str] = field(default_factory=list)
    date: str | None = None
    asset_type: str = "photo"
    entity_key: str | None = None
    entity_type: str | None = None
    query_kind: str | None = None   # source | exact | context


def date_span(value: str | None) -> tuple[str | None, str | None]:
    """A provider's date ("1892-08-04", or a range "1900-01-01/1900-12-31") as the asset's start
    and end date; each fits the column."""
    if not value:
        return None, None
    start, _, end = value.partition("/")
    return (start.strip()[:20] or None), (end.strip()[:20] or None)


def source_candidate_has_relevance(candidate: Candidate, case: Case) -> bool:
    """Require an image-level case signal for source-page candidates."""
    try:
        expected_values = (json.loads(case.people_json or "[]")
                           + json.loads(case.aliases_json or "[]"))
    except (TypeError, json.JSONDecodeError):
        expected_values = []
    expected = {
        token.lower() for value in expected_values
        for token in re.findall(r"[\w'-]+", str(value)) if len(token) >= 4
    }
    if not expected:
        return True
    image_text = " ".join(filter(None, [
        candidate.caption,
        urlparse(candidate.url).path,
    ])).lower()
    return any(token in image_text for token in expected)


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def _clean(text: str | None, n: int = 600) -> str | None:
    t = " ".join(_strip_html(text).split())
    return t[:n] or None


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


_QUERY_STOP = {"a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "with", "from",
               "by", "into", "inside", "outside", "near", "during", "after", "before", "his",
               "her", "their", "its", "is", "was", "photo", "photograph", "image", "picture"}


def core_query(query: str, words: int = 3) -> str | None:
    """The first content words of a long query ('police drone and K9
    search residential property night' -> 'police drone K9'): Commons
    search needs every word to match, so a sentence-long query finds
    nothing. None when the query is already short."""
    toks = [t for t in re.findall(r"[\w'-]+", query or "") if t.lower() not in _QUERY_STOP]
    if len(toks) <= words:
        return None
    return " ".join(toks[:words])


def category_matches(name: str, category: str) -> bool:
    """A Commons category belongs to an entity only when every word of the
    entity's name is in the category title ('Lizzie Borden' ->
    'Category:Lizzie Borden', not 'Category:Borden, Ontario')."""
    words = set(re.findall(r"\w+", name.lower()))
    return bool(words) and words <= set(re.findall(r"\w+", category.lower()))


class WikimediaProvider:
    name = "wikimedia"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.cfg = ai_config.visual_search

    _last = 0.0

    async def search(self, query: str, limit: int) -> list[Candidate]:
        out = await self._search(query, limit)
        short = None if out else core_query(query)
        if short:
            # nothing for the whole sentence: once more with its core words
            out = [c for c in await self._search(short, limit)]
            for c in out:
                c.found_for = query
        return out

    async def _get(self, params: dict) -> dict:
        import asyncio
        import time

        wait = self.cfg.min_request_interval_s - (time.monotonic() - WikimediaProvider._last)
        if wait > 0:
            await asyncio.sleep(wait)
        WikimediaProvider._last = time.monotonic()
        r = await self.client.get(self.cfg.wikimedia_api, params={"format": "json", **params})
        r.raise_for_status()
        return r.json()

    async def category_files(self, name: str, limit: int) -> list[Candidate]:
        """The files of the Commons category named like `name` (the best
        category match for it) and of its direct subcategories, at most
        `limit`: a curated set of everything Commons holds on the subject."""
        try:
            found = await self._get({
                "action": "query", "list": "search", "srsearch": name,
                "srnamespace": 14, "srlimit": 1})
            hits = (found.get("query") or {}).get("search") or []
            if not hits or not category_matches(name, hits[0]["title"]):
                return []
            category = hits[0]["title"]
            subs = await self._get({
                "action": "query", "list": "categorymembers", "cmtitle": category,
                "cmtype": "subcat", "cmlimit": 10})
            titles = [category] + [m["title"] for m in
                                   (subs.get("query") or {}).get("categorymembers") or []]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("wikimedia category search failed for %r: %s", name, e)
            return []
        out: list[Candidate] = []
        for title in titles:
            if len(out) >= limit:
                break
            out.extend(await self._pages({
                "generator": "categorymembers", "gcmtitle": title, "gcmtype": "file",
                "gcmlimit": limit - len(out)}, name))
        return out[:limit]

    async def _search(self, query: str, limit: int) -> list[Candidate]:
        return await self._pages({
            "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": 6, "gsrlimit": limit}, query)

    async def _pages(self, generator: dict, query: str) -> list[Candidate]:
        params = {
            "action": "query", **generator, "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 2000,
        }
        try:
            pages = ((await self._get(params)).get("query") or {}).get("pages") or {}
        except (httpx.HTTPError, ValueError) as e:
            log.warning("wikimedia search failed for %r: %s", query, e)
            return []
        out = []
        for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if (info.get("width") or 0) < self.cfg.min_width:
                continue
            meta = info.get("extmetadata") or {}

            def mv(key):
                return (meta.get(key) or {}).get("value")

            artist = _clean(mv("Artist"), 200)
            out.append(Candidate(
                url=info.get("thumburl") or info.get("url"),
                page_url=info.get("descriptionurl"), provider=self.name,
                title=(page.get("title") or "").removeprefix("File:"),
                caption=_clean(mv("ImageDescription")),
                license=_clean(mv("LicenseShortName"), 120),
                credit=f"{artist or 'Unknown author'} / Wikimedia Commons",
                source_name="Wikimedia Commons", found_for=query,
                date=_clean(mv("DateTimeOriginal"), 40),
            ))
        return out


_IMG_ATTR = re.compile(r"""<img\b[^>]*>""", re.I | re.S)
_FIGURE = re.compile(r"<figure\b.*?</figure>", re.I | re.S)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}\s*=\s*["']([^"']+)["']""", tag, re.I)
    return html.unescape(m.group(1)) if m else None


def _meta(page: str, prop: str) -> str | None:
    for pat in (
        rf"""<meta[^>]+(?:property|name)\s*=\s*["']{prop}["'][^>]*content\s*=\s*["']([^"']+)""",
        rf"""<meta[^>]+content\s*=\s*["']([^"']+)["'][^>]*(?:property|name)\s*=\s*["']{prop}["']""",
    ):
        m = re.search(pat, page, re.I)
        if m:
            return html.unescape(m.group(1))
    return None


def page_images(page: str, base_url: str, max_images: int = 4) -> list[dict]:
    """Editorial images of an article: og/twitter image and figures with
    captions (decorative icons, logos and tiny images are skipped)."""
    found: list[dict] = []
    seen: set[str] = set()

    def add(src, caption, alt=None):
        if not src or src.startswith("data:"):
            return
        url = urljoin(base_url, src)
        low = url.lower()
        if any(x in low for x in ("logo", "icon", "sprite", "avatar", "pixel",
                                  "placeholder", ".svg", ".gif")):
            return
        if url in seen:
            return
        seen.add(url)
        found.append({"url": url, "caption": _clean(caption) or _clean(alt)})

    og_title = _meta(page, "og:title")
    add(_meta(page, "og:image"), _meta(page, "og:image:alt") or og_title)
    add(_meta(page, "twitter:image"), og_title)
    for fig in _FIGURE.findall(page):
        img = _IMG_ATTR.search(fig)
        if not img:
            continue
        tag = img.group(0)
        src = _attr(tag, "data-src") or _attr(tag, "src")
        cap = re.search(r"<figcaption\b[^>]*>(.*?)</figcaption>", fig, re.I | re.S)
        add(src, cap.group(1) if cap else None, _attr(tag, "alt"))
    for tag in _IMG_ATTR.findall(page):
        w = _attr(tag, "width")
        if w and w.isdigit() and int(w) >= 500:
            add(_attr(tag, "data-src") or _attr(tag, "src"), None, _attr(tag, "alt"))
    return found[:max_images]


class SourcePageProvider:
    name = "source_page"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def images_for(self, source: Source) -> list[Candidate]:
        url = source.url or ""
        if not url.startswith("http") or any(
                h in url for h in ("youtube.com", "youtu.be")):
            return []
        try:
            r = await self.client.get(url)
            r.raise_for_status()
            if "html" not in r.headers.get("content-type", "html"):
                return []
            page = r.text
        except httpx.HTTPError as e:
            log.info("source page fetch failed %s: %s", url, type(e).__name__)
            return []
        return [
            Candidate(url=img["url"], page_url=url, provider=self.name,
                      title=source.title, caption=img["caption"],
                      source_name=source.publisher or urlparse(url).hostname,
                      found_for=f"source:{source.id}")
            for img in page_images(page, url)
        ]


def brave_backend():
    """Brave Search (the project's only search provider), configured as the
    research engine configures it."""
    from app.core.env import env_value
    from app.research_engine.search_adapters.brave import BraveSearchBackend

    cfg = ai_config.search_engine
    return BraveSearchBackend(
        api_key=env_value(cfg.brave_api_key_env), base_url=cfg.brave_base_url,
        timeout_s=cfg.request_timeout_s, health_cache_seconds=cfg.health_cache_seconds,
        result_count=cfg.max_results_per_query, min_interval_s=cfg.search_min_interval_s)


class BraveImageProvider:
    """Brave image search for the case's own people, places and documents.
    The result carries no license: rights default to editorial_review_required
    (rights.py), so a human decides before it can be used."""
    name = "brave_images"

    def __init__(self, backend):
        self.backend = backend
        self.cfg = ai_config.visual_search

    async def search(self, query: str, limit: int) -> list[Candidate]:
        from app.research_engine.search import SearchError

        try:
            results = await self.backend.search_media("images", query, "en", limit)
        except SearchError as e:
            log.warning("brave image search failed for %r: %s", query, e)
            return []
        out = []
        for r in results:
            props = r.get("properties") or {}
            url = props.get("url") or (r.get("thumbnail") or {}).get("src")
            if not url or (props.get("width") and props["width"] < self.cfg.min_width):
                continue
            out.append(Candidate(
                url=url, provider=self.name, page_url=r.get("url"),
                title=_clean(r.get("title"), 300), source_name=r.get("source"),
                found_for=query))
        return out


def _owner_matches(owner: str, url: str, title: str | None) -> bool:
    """The page is plausibly the named owner's own: a distinctive word of the
    owner's name appears in the page's domain or title."""
    words = {w.lower() for w in re.findall(r"[\w']+", owner or "") if len(w) >= 4}
    haystack = f"{urlparse(url).hostname or ''} {title or ''}".lower()
    return any(w in haystack for w in words)


class LeadSourceProvider:
    """Part 4b(2): the ORIGINAL owner of footage or photos a video showed
    (court, police, archive, news organisation) — searched on the web and its
    page fetched directly, never the YouTube video itself."""
    name = "lead_source"

    def __init__(self, client: httpx.AsyncClient, backend):
        self.client = client
        self.backend = backend
        self.cfg = ai_config.visual_search

    async def images_for(self, lead, case: Case) -> list[Candidate]:
        from app.research_engine.search import SearchError

        owner = (lead.original_owner or "").strip()
        if not owner:
            return []
        try:
            results = await self.backend.search(f"{owner} {case.canonical_title}", "en", limit=6)
        except SearchError as e:
            log.info("lead source search failed for %r: %s", owner, e)
            return []
        out: list[Candidate] = []
        pages = 0
        for r in results:
            if pages >= self.cfg.max_lead_pages_per_lead:
                break
            if any(h in r.url for h in ("youtube.com", "youtu.be")) or not _owner_matches(
                    owner, r.url, r.title):
                continue
            pages += 1
            try:
                resp = await self.client.get(r.url)
                resp.raise_for_status()
                page = resp.text if "html" in resp.headers.get("content-type", "html") else ""
            except httpx.HTTPError as e:
                log.info("lead source page fetch failed %s: %s", r.url, type(e).__name__)
                continue
            for img in page_images(page, r.url):
                out.append(Candidate(
                    url=img["url"], page_url=r.url, provider=self.name,
                    title=r.title, caption=img["caption"] or _clean(lead.description_en),
                    source_name=owner, found_for=f"lead:{lead.id}", query_kind="source"))
        return out


async def store_video_links(db: Session, case: Case, backend, queries: list[dict]) -> int:
    """Part 4b(3): Brave video search results as LINKS ONLY for the owner."""
    from app.db.models import MediaLink
    from app.research_engine.search import SearchError

    cfg = ai_config.visual_search
    known = {u for (u,) in db.query(MediaLink.url).filter(MediaLink.case_id == case.id)}
    added = 0
    for q in queries[: cfg.max_video_link_queries]:
        try:
            results = await backend.search_media(
                "videos", q["query"], "en", cfg.video_links_per_query)
        except SearchError as e:
            log.info("brave video search failed for %r: %s", q["query"], e)
            continue
        for r in results:
            url = (r.get("url") or "").strip()
            if not url or url in known:
                continue
            known.add(url)
            video = r.get("video") or {}
            db.add(MediaLink(
                case_id=case.id, kind="video", url=url[:1000],
                title=_clean(r.get("title"), 500), description=_clean(r.get("description")),
                creator=(video.get("creator") or None), duration=video.get("duration"),
                platform=urlparse(url).hostname, found_for=q["query"][:500]))
            added += 1
    db.commit()
    return added


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------


def next_asset_code(db: Session) -> str:
    last = db.query(VisualAsset).order_by(VisualAsset.id.desc()).first()
    return f"VIS_{(last.id if last else 0) + 1:06d}"


# Providers asked for CONTEXT queries: rights are known per file.
RIGHTS_KNOWN_PROVIDERS = ("wikimedia",)
# Within one run: case sources first, then exact finds, then context.
_KIND_ORDER = {"source": 0, "exact": 1, "context": 2}


def query_kind(q: dict) -> str:
    return "context" if q.get("kind") == "context" else "exact"


def footage_queries(queries: list[dict], max_queries: int) -> list[dict]:
    from app.documentary.visuals.footage import footage_queries as fq

    return fq(queries, max_queries)


def add_uploaded_image(db: Session, case: Case, data: bytes, filename: str,
                       title: str | None = None, caption: str | None = None,
                       asset_role: str = "evidence", rights_status: str = "owned",
                       asset_type: str = "photo") -> VisualAsset:
    """A user-supplied file (rights asserted by the uploader)."""
    img = IM.open_image(data)
    code = next_asset_code(db)
    master = IM.save_master(img, storage.visuals_dir(case.id, asset_type) / f"{code}.jpg")
    thumb = IM.save_thumbnail(img, storage.thumbs_dir(case.id) / f"{code}.jpg")
    asset = VisualAsset(
        case_id=case.id, asset_code=code, asset_type=asset_type,
        title=title or filename, caption=caption, provider="upload",
        rights_status=rights_status if rights_status in R.RIGHTS else "owned",
        rights_reason="uploaded by the production", asset_role=asset_role,
        local_path=storage.rel(master), thumbnail_path=storage.rel(thumb),
        width=img.width, height=img.height, phash=IM.dhash(img), sha256=IM.sha256(data),
        # checked like everything else before it may go on screen
        verification_status="unverified",
        relevance_tier=T.provisional_tier("upload", asset_role),
        case_relevance=T.tier_label(T.provisional_tier("upload", asset_role)),
        found_during="upload",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def __getattr__(name: str):
    # the agent class lives in app/agents/visual_research.py (imported when first asked for)
    if name == "VisualResearchAgent":
        from app.agents.visual_research import VisualResearchAgent

        return VisualResearchAgent
    raise AttributeError(name)

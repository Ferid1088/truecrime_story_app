"""Content extraction (Parts 13-15).

Separates article/document text from page chrome, preserves metadata,
never alters wording. Classifies each document into an honest
content_status: full_text | partial_text | metadata_only | unavailable.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_MIN_FULL_CHARS = 1200       # tuned against config at call sites
_MIN_PARTIAL_CHARS = 300


@dataclass
class ExtractedDocument:
    text: str | None
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    canonical_url: str | None = None
    meta_description: str | None = None
    page_count: int | None = None
    language_meta: str | None = None
    content_status: str = "unavailable"   # full|partial|metadata|unavailable
    extractor: str | None = None
    meta: dict = field(default_factory=dict)


def extract(
    body: bytes,
    content_type: str | None,
    url: str,
    min_full_chars: int = _MIN_FULL_CHARS,
) -> ExtractedDocument:
    """Dispatch on content type; returns an ExtractedDocument whose
    content_status honestly reflects what was retrievable."""
    if not body:
        return ExtractedDocument(text=None, content_status="unavailable")
    ctype = (content_type or "").lower()
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        return _extract_pdf(body, url, min_full_chars)
    return _extract_html(body, url, min_full_chars)


def _extract_html(body: bytes, url: str,
                  min_full_chars: int) -> ExtractedDocument:
    import trafilatura
    from trafilatura.metadata import extract_metadata

    html = _decode(body)
    doc = ExtractedDocument(text=None, extractor="trafilatura")

    try:
        meta = extract_metadata(html, default_url=url)
        if meta is not None:
            doc.title = (meta.title or "").strip() or None
            doc.author = (meta.author or "").strip() or None
            doc.publisher = (meta.sitename or "").strip() or None
            doc.published_at = meta.date or None
            doc.canonical_url = (meta.url or "").strip() or None
            doc.meta_description = (meta.description or "").strip() or None
            doc.language_meta = (getattr(meta, "language", None) or None)
    except Exception as e:  # noqa: BLE001 - metadata is best-effort
        log.debug("metadata extraction failed for %s: %s", url, e)
        _html_fallback_meta(html, doc)

    if not doc.title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            doc.title = re.sub(r"\s+", " ", m.group(1)).strip()[:500] or None

    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_recall=True,
            url=url,
        )
    except Exception as e:  # noqa: BLE001
        log.debug("trafilatura extract failed for %s: %s", url, e)
        text = None

    text = (text or "").strip() or None
    doc.text = text
    doc.content_status = _classify(text, min_full_chars,
                                   has_meta=bool(doc.title or doc.meta_description))
    return doc


def _extract_pdf(body: bytes, url: str,
                 min_full_chars: int) -> ExtractedDocument:
    from pypdf import PdfReader

    doc = ExtractedDocument(text=None, extractor="pypdf")
    try:
        reader = PdfReader(io.BytesIO(body))
    except Exception as e:  # noqa: BLE001
        log.debug("pdf parse failed for %s: %s", url, e)
        return doc

    doc.page_count = len(reader.pages)
    try:
        info = reader.metadata or {}
        doc.title = (info.get("/Title") or "").strip() or None
        doc.author = (info.get("/Author") or "").strip() or None
    except Exception:  # noqa: BLE001
        pass

    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - skip unreadable page
            continue
        t = t.strip()
        if t:
            pages.append(f"[page {i + 1}]\n{t}")
    text = "\n\n".join(pages).strip() or None
    doc.text = text
    doc.content_status = _classify(text, min_full_chars,
                                   has_meta=bool(doc.title))
    return doc


def _classify(text: str | None, min_full_chars: int,
              has_meta: bool) -> str:
    if not text:
        return "metadata_only" if has_meta else "unavailable"
    if len(text) >= min_full_chars:
        return "full_text"
    if len(text) >= _MIN_PARTIAL_CHARS:
        return "partial_text"
    return "metadata_only" if has_meta else "unavailable"


def _decode(body: bytes) -> str:
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _html_fallback_meta(html: str, doc: ExtractedDocument):
    """Cheap og:/canonical scraping when trafilatura metadata fails."""
    def _prop(name: str) -> str | None:
        m = re.search(
            r'<meta[^>]+(?:property|name)=["\']' + re.escape(name) +
            r'["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:property|name)=["\']'
                + re.escape(name) + r'["\']', html, re.I | re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else None

    doc.title = doc.title or _prop("og:title")
    doc.publisher = doc.publisher or _prop("og:site_name")
    doc.meta_description = doc.meta_description or (
        _prop("og:description") or _prop("description"))
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](.*?)["\']',
                  html, re.I | re.S)
    if m and not doc.canonical_url:
        doc.canonical_url = m.group(1).strip()
    m = re.search(r'<html[^>]+lang=["\']([\w-]+)["\']', html, re.I)
    if m and not doc.language_meta:
        doc.language_meta = m.group(1)

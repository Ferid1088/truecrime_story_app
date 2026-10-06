"""URL canonicalization (Part 8).

Preserves the full provenance chain: original_url -> canonical_url
(dedupe key) and, after fetching, final_url (post-redirect target).
"""
from __future__ import annotations

import re
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

# Tracking/boilerplate parameters that never change content.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "utm_creative_format",
    "utm_marketing_tactic", "gclid", "fbclid", "dclid", "msclkid",
    "mc_cid", "mc_eid", "igshid", "igsh", "spm", "ref", "ref_src",
    "ref_url", "source", "campaign_id", "campaignid", "cmpid", "cmp",
    "ocid", "ncid", "srsltid", "ved", "usg", "ei", "sa", "sca_esv",
    "_ga", "_gl", "twclid", "wickedid", "vero_id", "yclid", "guccounter",
}
_TRACKING_PREFIXES = ("utm_", "pk_", "piwik_", "matomo_", "ga_")

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _drop_tracking(query: str) -> str:
    if not query:
        return ""
    kept = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
        and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    kept.sort()
    return urlencode(kept)


def youtube_video_id(url: str) -> str | None:
    """Extract a YouTube video id from any known URL variant."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if host in ("youtu.be",):
        cand = path.lstrip("/").split("/")[0]
        return cand if _YT_ID.match(cand or "") else None
    if host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        if path == "/watch":
            q = dict(parse_qsl(parts.query))
            vid = q.get("v", "")
            return vid if _YT_ID.match(vid) else None
        for prefix in ("/embed/", "/shorts/", "/live/", "/v/"):
            if path.startswith(prefix):
                cand = path[len(prefix):].split("/")[0]
                return cand if _YT_ID.match(cand or "") else None
    return None


def canonicalize_url(url: str) -> str | None:
    """Canonical dedupe key for a URL.

    - youtube variants -> youtube.com/watch?v=<id>
    - scheme -> https, host lowercased, www. stripped
    - mobile subdomains normalized (m., mobile., amp.)
    - tracking params removed, remaining params sorted
    - fragments removed, trailing slash dropped
    Returns None for unusable URLs.
    """
    u = (url or "").strip()
    if not u:
        return None
    vid = youtube_video_id(u)
    if vid:
        return f"youtube.com/watch?v={vid}"
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    scheme = (parts.scheme or "https").lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    for prefix in ("www.", "m.", "mobile.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    # Drop AMP variants of article paths.
    path = re.sub(r"/amp/?$", "", path) or "/"
    if path != "/":
        path = path.rstrip("/")
    port = parts.port
    netloc = host
    if port and port not in (80, 443):
        netloc = f"{host}:{port}"
    return urlunsplit(
        ("https", netloc, path, _drop_tracking(parts.query), "")
    ).replace("https://", "", 1)


def normalize_for_display(url: str) -> str:
    """Cleaned URL safe to store/show (https, tracking stripped)."""
    canon = canonicalize_url(url)
    return f"https://{canon}" if canon else (url or "").strip()


def domain_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host

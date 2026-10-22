"""P2: automatic official-source discovery for YouTube clips — no owner approval.

P2a `derive_official_bodies`: the official bodies of a case (police, sheriff, prosecutor,
court, state police, FBI ...) are read out of the case's own facts and dossier names by the
configured markers; every body stores the evidence where its name came from.

P2b `prove_channels`: for each body, channel candidates come from a budgeted Data API
channel search and are proven by stored evidence scored in config — the channel links to the
body's official domain, or the official site links back to the channel (at least one domain
link is mandatory); name match, a large channel and official-looking uploads only add to the
score. Anything below the threshold is skipped with the reason stored — never queued, never
guessed.

P2c `search_official_channel_videos`: a proven channel's uploads (cheap playlistItems) are
filtered by the case's entity names and the configured material kinds (press conference,
statement, hearing, bodycam release, 911 call, sentencing, plea ...) and run through the
existing chain: the classifier, `VideoSource` upsert, transcript storage. A video of a proven
channel is classed `official_body` and carries the channel's proof into its source record.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Sequence
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import EntityDossier, Fact, OfficialBody, VideoSource
from app.documentary.visuals import youtube_quota as Q
from app.documentary.visuals.youtube_discovery import (
    _fetch_and_store_transcript,
    _get,
    channel_uploads,
    classify_video,
    upsert_discovered,
    video_details,
)
from app.utils import utc_now

log = logging.getLogger("truecrime.official_sources")

# words that may sit before a body's name in a fact but never belong to it
_PREFIX_STOP = {
    "the", "a", "an", "and", "or", "of", "for", "to", "by", "with", "in", "at", "on",
    "including", "that", "which", "who", "said", "says", "stated", "filed", "during",
    "according", "was", "were", "is", "are", "his", "her", "its", "their", "after",
    "before", "where", "when", "from", "into", "over", "both", "either", "neither",
}


def _name_key(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[''’`.]", "",
                                     name.lower().replace("'s office", "").replace(
                                         "’s office", "").strip()))


def _bodies_in_text(text: str, cfg) -> list[tuple[str, str]]:
    """(name, body_type) pairs found in one piece of text: a run of capitalized words in
    front of a configured body marker, or a standalone federal-body acronym."""
    out: list[tuple[str, str]] = []
    if not text:
        return out
    markers = sorted(cfg.body_markers, key=len, reverse=True)
    marker_re = "(?:" + "|".join(re.escape(m) for m in markers) + ")"
    for m in re.finditer(
            rf"([A-Z][\w.&-]*(?:\s+(?:of the|of|and|&)\s+[A-Z][\w.&-]*|\s+[A-Z][\w.&-]*)*)\s+(?i:({marker_re}))\b",
            text):
        run, marker = m.group(1), m.group(2)
        words = [w for w in run.split()
                 if w.lower().strip(".,") not in _PREFIX_STOP or w.isupper()]
        name = (" ".join(words) + " " + marker).strip(" ,.:'\"")
        if not name or name[0].isdigit():
            continue
        # a marker alone ("in court", "a police officer") is no body name: the run must
        # have given the name at least one capitalized word of its own
        if not words or not any(w[:1].isupper() and len(w) > 2 for w in words):
            continue
        if marker.lower() == "court" and len(words) <= 1 and words[0].lower() in _PREFIX_STOP:
            continue
        out.append((name, cfg.body_markers[marker.lower()]))
    for standalone in cfg.standalone_bodies:
        if re.search(rf"\b{re.escape(standalone)}\b", text):
            out.append((standalone, "federal_agency"))
    return out


def derive_official_bodies(db: Session, case) -> dict:
    """Read the official bodies out of the case's facts and dossier names and store them as
    OfficialBody rows (status 'found'), evidence attached. Idempotent: existing rows keep
    their status/proof and only gain new evidence."""
    cfg = ai_config.official_sources
    texts: list[tuple[str, int | None]] = []   # (text, fact_id)
    for f in db.query(Fact).filter(Fact.case_id == case.id).all():
        texts.append((f.claim or "", f.id))
    for d in db.query(EntityDossier).filter(EntityDossier.case_id == case.id).all():
        texts.append((d.canonical_name or "", None))
    found: dict[str, dict] = {}
    for text, fact_id in texts:
        for name, body_type in _bodies_in_text(text, cfg):
            key = _name_key(name)
            row = found.get(key)
            if row is None:
                row = found[key] = {"name": name, "body_type": body_type,
                                    "fact_ids": [], "claims": []}
            if fact_id and fact_id not in row["fact_ids"]:
                row["fact_ids"].append(fact_id)
                row["claims"].append(text[:200])
            # a longer name for the same key prefix wins ("Moscow Police Department"
            # absorbs "Moscow Police")
    # merge subset names: "moscow police" folds into "moscow police department"
    for key in list(found):
        for other in list(found):
            if key != other and key + " department" == other:
                found[other]["fact_ids"] = sorted(set(found[other]["fact_ids"] + found[key]["fact_ids"]))
                found[other]["claims"] = (found[other]["claims"] + found[key]["claims"])[:5]
                del found[key]
                break
    stats = {"found": 0, "existing": 0}
    existing = {b.name_key: b for b in db.query(OfficialBody).filter_by(case_id=case.id)}
    for key, info in found.items():
        evidence = json.dumps({"fact_ids": sorted(set(info["fact_ids"])),
                               "claims": info["claims"][:5]}, ensure_ascii=False)
        if key in existing:
            b = existing[key]
            merged = json.loads(b.evidence_json or "{}")
            merged["fact_ids"] = sorted(set(merged.get("fact_ids") or []) | set(info["fact_ids"]))
            merged["claims"] = ((merged.get("claims") or []) + info["claims"])[:5]
            b.evidence_json = json.dumps(merged, ensure_ascii=False)
            stats["existing"] += 1
        else:
            db.add(OfficialBody(case_id=case.id, name=info["name"], name_key=key,
                                body_type=info["body_type"], status="found",
                                evidence_json=evidence))
            stats["found"] += 1
    db.commit()
    return stats


# ---- P2b: prove the channel --------------------------------------------------------------------

async def search_channels(client: httpx.AsyncClient, db: Session, case_id: int | None,
                          query: str, api_key: str) -> list[dict] | None:
    """search.list type=channel — same budget gates as video search: discovery share and the
    daily search-call cap; None means 'not even tried'."""
    cfg = ai_config.youtube_data_api
    if not Q.discovery_status(db)["available"]:
        return None
    if Q.search_calls_used_today(db, stage="discovery") >= cfg.daily_search_calls:
        return None
    data = await _get(client, db, case_id, "discovery", "search",
                      {"part": "snippet", "type": "channel", "q": query,
                       "maxResults": ai_config.official_sources.channel_search_results},
                      api_key)
    return (data or {}).get("items") or []


async def channel_details(client: httpx.AsyncClient, db: Session, case_id: int | None,
                          channel_ids: list[str], api_key: str) -> list[dict]:
    data = await _get(client, db, case_id, "discovery", "channels",
                      {"part": "snippet,brandingSettings,statistics,contentDetails",
                       "id": ",".join(channel_ids)}, api_key)
    return (data or {}).get("items") or []


def _channel_links(ch: dict) -> list[str]:
    """URLs the channel itself publishes: description links and the brandingSettings links."""
    snippet = ch.get("snippet") or {}
    branding = ((ch.get("brandingSettings") or {}).get("channel")) or {}
    urls = [u.lower() for u in re.findall(r"https?://[^\s)>'\"]+",
                                          str(snippet.get("description") or ""))]
    for link in branding.get("links") or []:
        if isinstance(link, dict) and link.get("targetUrl"):
            urls.append(str(link["targetUrl"]).lower())
    return urls


def _official_domain(urls: list[str], cfg) -> str | None:
    """The first linked host that looks official by suffix (.gov/.us/… configured)."""
    for url in urls:
        host = (urlparse(url).netloc or "").lower()
        if any(host.endswith(suffix) or suffix.lstrip(".") in host
               for suffix in cfg.official_domain_suffixes):
            return host
    return None


async def _domain_links_back(client: httpx.AsyncClient, domain: str | None, ch: dict,
                             cfg) -> bool:
    """The official site's own page links back to this YouTube channel."""
    if not domain:
        return False
    cid = str(ch.get("id") or "").lower()
    handle = str(((ch.get("snippet") or {}).get("customUrl")) or "").lstrip("@").lower()
    try:
        r = await client.get(f"https://{domain}", timeout=cfg.domain_fetch_seconds,
                             follow_redirects=True)
        if r.status_code != 200:
            return False
        for target in re.findall(r"(?:youtube\.com|youtu\.be)/(?:channel/|@|c/|user/)([\w.-]+)",
                                 r.text.lower()):
            if target and target in {cid, handle}:
                return True
        return False
    except Exception:  # noqa: BLE001 — an unreachable site is not evidence
        return False


def _name_match(body_name: str, channel_title: str) -> bool:
    stop = {"the", "of", "a", "an", "and", "city", "county", "state", "office", "department",
            "dept"}
    body_tokens = {w for w in re.findall(r"[a-z]{3,}", body_name.lower()) if w not in stop}
    ch_tokens = set(re.findall(r"[a-z]{3,}", channel_title.lower()))
    if not body_tokens:
        return _name_key(body_name) == _name_key(channel_title)
    return body_tokens <= ch_tokens or bool(ch_tokens and ch_tokens <= body_tokens)


def _official_uploads_share(titles: list[str], cfg) -> bool:
    if not titles:
        return False
    hits = sum(1 for t in titles if any(k in t.lower() for k in cfg.material_kinds))
    return hits / len(titles) >= cfg.official_upload_share


async def _official_site_domains(client, body_name: str, cfg,
                                 search_backend=None) -> list[str]:
    """The body's known domain, looked up with Brave (the project's only search engine) —
    non-social hosts of the top results, used only for the backlink check, which is the
    real proof. None/[] when Brave is unavailable or answers nothing."""
    if search_backend is None:
        return []
    try:
        results = await search_backend.search(f"{body_name} official website", "en",
                                              limit=cfg.channel_search_results)
    except Exception:  # noqa: BLE001 — search failure is not evidence either way
        return []
    hosts = []
    for r in results:
        host = (urlparse(r.url).netloc or "").lower()
        if host and not any(s in host for s in ("youtube.com", "facebook.com", "twitter.com",
                                                "x.com", "instagram.com", "linkedin.com",
                                                "wikipedia.org", "reddit.com")):
            hosts.append(host)
    return hosts


async def prove_channels(client: httpx.AsyncClient, db: Session, case, api_key: str,
                         search_backend=None) -> dict:
    """Score every channel candidate for every unproven body; the best one wins the row when
    it reaches the configured score AND has a domain link either way."""
    cfg = ai_config.official_sources
    stats = {"proven": 0, "skipped": 0, "bodies": 0}
    for body in db.query(OfficialBody).filter_by(case_id=case.id, status="found"):
        stats["bodies"] += 1
        items = await search_channels(client, db, case.id, body.name, api_key)
        if items is None:
            body.status = "skipped"
            body.skip_reason = "discovery share or search-call budget spent"
            db.commit()
            break
        ids = [_ for _ in ((i.get("id") or {}).get("channelId") for i in items) if _]
        details = await channel_details(client, db, case.id, ids, api_key) if ids else []
        brave_hosts: list[str] | None = None
        record: dict[str, list[dict]] = {"candidates": []}
        best: tuple[int, dict, dict] | None = None   # score, checks, channel
        for ch in details:
            title = str((ch.get("snippet") or {}).get("title") or "")
            links = _channel_links(ch)
            official_suffix_host = _official_domain(links, cfg)
            backlink_host = None
            backlink_via_channel_link = False
            for host in [official_suffix_host, _first_linked_host(links)]:
                # a non-suffix domain only counts when its page links back to the channel
                if host and await _domain_links_back(client, host, ch, cfg):
                    backlink_host = host
                    backlink_via_channel_link = True
                    break
            name_match = _name_match(body.name, title)
            if backlink_host is None and name_match:
                # the channel names the body but publishes no link — find the body's own
                # domain (Brave) and let the site prove the channel by linking back
                if brave_hosts is None:
                    brave_hosts = await _official_site_domains(
                        client, body.name, cfg, search_backend)
                for host in brave_hosts:
                    if await _domain_links_back(client, host, ch, cfg):
                        backlink_host = host
                        break
            official_domain = official_suffix_host or backlink_host
            checks = {
                "channel_links_to_domain": (official_suffix_host is not None
                                            or backlink_via_channel_link),
                "domain_links_to_channel": backlink_host is not None,
                "name_match": name_match,
                "large_channel": int((ch.get("statistics") or {}).get("subscriberCount") or 0)
                                 >= cfg.large_channel_subscribers,
            }
            # upload titles are checked only for candidates that already look right —
            # the extra playlistItems call is not spent on a wrong name
            if checks["name_match"] or checks["channel_links_to_domain"]:
                uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
                if uploads:
                    items2 = await _get(client, db, case.id, "discovery", "playlistItems",
                                        {"part": "snippet", "playlistId": uploads,
                                         "maxResults": cfg.upload_titles_sampled}, api_key)
                    titles = [str((i.get("snippet") or {}).get("title") or "")
                              for i in (items2 or {}).get("items") or []]
                    checks["official_uploads"] = _official_uploads_share(titles, cfg)
            score = sum(cfg.proof_scores.get(k, 0) for k, v in checks.items() if v)
            cand = {"channel_id": ch.get("id"), "title": title, "checks": checks,
                    "score": score, "domain": official_domain}
            record["candidates"].append(cand)
            if best is None or score > best[0]:
                best = (score, checks, cand)
        domain_proof = best and (best[1]["channel_links_to_domain"]
                                 or best[1]["domain_links_to_channel"])
        if best and best[0] >= cfg.proof_min_score and (
                domain_proof or not cfg.proof_requires_domain_link):
            score, checks, cand = best
            body.status = "proven"
            body.channel_id = cand["channel_id"]
            body.channel_title = cand["title"]
            body.channel_url = f"https://www.youtube.com/channel/{cand['channel_id']}"
            body.uploads_playlist_id = None
            body.proved_at = utc_now()
            body.proof_json = json.dumps(
                {"status": "proven", "score": score, "checks": checks,
                 "official_domain": cand["domain"], "candidates": record["candidates"],
                 "proved_at": body.proved_at.isoformat()}, ensure_ascii=False)
            stats["proven"] += 1
        else:
            body.status = "skipped"
            body.skip_reason = (f"best score {best[0] if best else 0} below "
                                f"{cfg.proof_min_score} or no domain link")[:200]
            body.proof_json = json.dumps(
                {"status": "skipped", "candidates": record["candidates"]}, ensure_ascii=False)
            stats["skipped"] += 1
        db.commit()
    return stats


def _first_linked_host(urls: list[str]) -> str | None:
    for url in urls:
        host = (urlparse(url).netloc or "").lower()
        if host and not any(s in host for s in ("youtube.com", "facebook.com", "twitter.com",
                                                "x.com", "instagram.com", "linkedin.com")):
            return host
    return None


def _title_names_entity(title: str, terms: Sequence) -> str | None:
    """The entity term the upload title names: the full phrase, or every word of a
    multi-word term, or a distinctive single word (>=5 chars), or — for a canonical
    person name only — the surname (>=5 chars, never on a group alias like "the four
    victims"). Terms are (name, entity_type, canonical) triples; bare strings or
    (name, type) pairs are treated as non-canonical."""
    tl = title.lower()
    for term in terms:
        if isinstance(term, (list, tuple)):
            name = term[0] if len(term) > 0 else ""
            etype = term[1] if len(term) > 1 else "unknown"
            canonical = term[2] if len(term) > 2 else False
        else:
            name, etype, canonical = term, "unknown", False
        t = str(name).lower()
        if not t:
            continue
        if t in tl:
            return str(name)
        words = re.findall(r"[a-z0-9]{4,}", t)
        if len(words) >= 2 and all(w in tl for w in words):
            return str(name)
        if len(words) == 1 and len(words[0]) >= 5 and words[0] in tl:
            return str(name)
        if (canonical and etype == "person" and len(words) >= 2
                and len(words[-1]) >= 5 and words[-1] in tl):
            return str(name)
    return None


# ---- P2c: uploads of proven channels ------------------------------------------------------------

async def search_official_channel_videos(
        client: httpx.AsyncClient, db: Session, case,
        entity_terms: Sequence[str | tuple[str, str, bool]],
        api_key: str, transcript_provider=None) -> dict:
    """A proven channel's uploads filtered by the case's entity names and the configured
    material kinds, classified and stored like every discovered video — with the channel's
    stored proof carried onto the VideoSource."""
    cfg = ai_config.official_sources
    stats = {"channels": 0, "items": 0, "candidates": 0, "new": 0, "updated": 0,
             "transcripts": 0}
    for body in db.query(OfficialBody).filter_by(case_id=case.id, status="proven"):
        if not body.channel_id:
            continue
        stats["channels"] += 1
        proof = json.loads(body.proof_json or "{}")
        uploads = await channel_uploads(client, db, case.id, body.channel_id, api_key,
                                        max_pages=cfg.channel_upload_pages)
        # a clip is case material only when the upload title names a case entity —
        # phrase, all words of the name, or a distinctive surname (person); a generic
        # "announcement" title or one loose word like a state's name is never enough
        filtered = [i for i in uploads
                    if _title_names_entity(
                        str((i.get("snippet") or {}).get("title") or ""), entity_terms)]
        stats["items"] += len(filtered)
        ids = [v for v in
               (((i.get("snippet") or {}).get("resourceId") or {}).get("videoId")
                for i in filtered) if v]
        wanted = set(ids)
        for item in await video_details(client, db, case.id, ids, api_key):
            if item.get("id") not in wanted:
                continue                        # only what we asked for
            candidate = classify_video(item)
            candidate["channel_id"] = body.channel_id
            candidate["channel_proof"] = proof
            # the channel is proven official: the classifier sees the stored proof first
            from app.services.video_rights import VideoUseClassifier

            use = VideoUseClassifier().classify(candidate)
            candidate.update(use.fields())
            created = db.query(VideoSource).filter_by(
                case_id=case.id, video_id=candidate["video_id"]).first() is None
            row = upsert_discovered(db, case, candidate, f"official channel: {body.name}")
            row.channel_proof_json = json.dumps(
                {"status": "proven", "body": body.name, "score": proof.get("score"),
                 "checks": proof.get("checks"),
                 "official_domain": proof.get("official_domain"),
                 "channel_id": body.channel_id}, ensure_ascii=False)
            db.commit()
            stats["new" if created else "updated"] = stats.get(
                "new" if created else "updated", 0) + 1
            if candidate.get("use") == "film_candidate":
                stats["candidates"] += 1
                with contextlib.suppress(Exception):
                    stats["transcripts"] += 1 if await _fetch_and_store_transcript(
                        db, row, transcript_provider) else 0
    return stats

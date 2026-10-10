"""Case selection: RECENT + SOLVED + NEVER USED.

Pipeline position: Case Discovery -> Duplicate Checker -> Case Status
Verifier -> ranking. The discovery job (background, search engine side)
runs `prepare_candidates`: duplicates against everything the system ever
held (identity, not titles), then the status of the best candidates is
verified with targeted searches and the `case_status_verifier` role, then
candidates are ranked by recency x status. `ingest_candidates` (database
side) re-checks duplicates against the live database, stores every
suggestion with its reason — and the duplicates/filtered ones with theirs
(auditability) — and returns what the user sees.

The evidence gate (`gate_solved`) is shared with the unsolved-case
monitor: SOLVED needs enough confidence AND either independent sources
or an official one. A single weak article never solves a case.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.lifecycle.identity import Identity, IdentityIndex, candidate_identity, raw_identity
from app.lifecycle.status import SOLVED, UNDER_REVIEW, UNKNOWN, UNSOLVED, normalize_status

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# dates and recency
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, names in enumerate([
    (), ("jan", "january", "januar", "jänner"), ("feb", "february", "februar"),
    ("mar", "march", "märz", "maerz", "marz"), ("apr", "april"), ("may", "mai"),
    ("jun", "june", "juni"), ("jul", "july", "juli"), ("aug", "august"),
    ("sep", "sept", "september"), ("oct", "october", "oktober", "okt"),
    ("nov", "november"), ("dec", "december", "dezember", "dez")]) for m in names}


def parse_date(text: str | None) -> date | None:
    """A date from free text: 2024-03-05, 2024-03, 5 March 2024,
    March 2024, 2024 (a year alone counts as mid-year)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    m = re.search(r"\b(19|20)(\d\d)-(\d{1,2})(?:-(\d{1,2}))?\b", t)
    if m:
        y, mo = int(m.group(1) + m.group(2)), int(m.group(3))
        d = int(m.group(4) or 15)
        try:
            return date(y, max(1, min(mo, 12)), max(1, min(d, 28)))
        except ValueError:
            return None
    y = re.search(r"\b(19|20)(\d\d)\b", t)
    if not y:
        return None
    year = int(y.group(1) + y.group(2))
    for word in re.findall(r"[a-zäöü]+", t):
        if word in _MONTHS:
            day = re.search(r"\b(\d{1,2})\.?\s*" + word, t)
            return date(year, _MONTHS[word], int(day.group(1)) if day and
                        1 <= int(day.group(1)) <= 28 else 15)
    return date(year, 7, 1)


def newest_date(*texts: str | None) -> date | None:
    ds = [d for d in (parse_date(t) for t in texts) if d]
    return max(ds) if ds else None


def recency_score(newest: date | None, today: date | None = None) -> float:
    """1.0 today, halving every recency_half_life_days."""
    cfg = ai_config.case_selection
    if newest is None:
        return cfg.undated_recency
    today = today or datetime.now(timezone.utc).date()
    age = max((today - newest).days, 0)
    return round(math.pow(0.5, age / cfg.recency_half_life_days), 4)


def rank(status: str, newest: date | None, today: date | None = None) -> tuple[float, float]:
    """(rank score, recency) from one date."""
    rec = recency_score(newest, today)
    return status_score(status, rec), rec


def status_score(status: str, rec: float) -> float:
    weight = ai_config.case_selection.status_weights.get(normalize_status(status), 0.3)
    return round(rec * weight, 4)


def case_recency(incident: date | None, latest: date | None,
                 today: date | None = None) -> tuple[float, str]:
    """How new the CASE is: mostly the incident itself, partly its newest
    development (a 1996 killing convicted in 2026 is news, but not a new
    case). Only a development date known: slightly discounted."""
    w = ai_config.case_selection.incident_weight
    if incident and latest:
        rec = w * recency_score(incident, today) + (1 - w) * recency_score(latest, today)
        how = f"incident {incident.isoformat()}, latest development {latest.isoformat()}"
    elif incident:
        rec, how = recency_score(incident, today), f"incident {incident.isoformat()}"
    elif latest:
        rec = 0.85 * recency_score(latest, today)
        how = f"latest development {latest.isoformat()} (incident date unknown)"
    else:
        return ai_config.case_selection.undated_recency, "no date known"
    return round(rec, 4), how


# ---------------------------------------------------------------------------
# evidence gate (discovery + monitor)
# ---------------------------------------------------------------------------

_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "gv", "or", "ne", "go"}


def site_of(url: str) -> str:
    """Registrable domain (independence of sources)."""
    try:
        host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    except ValueError:
        return ""
    parts = [p for p in host.split(".") if p and p != "www"]
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_official(url: str) -> bool:
    from app.research_engine.source_quality import classify_source

    q = classify_source(url)
    if q.source_type in ("primary_official", "court_record", "police_record",
                         "coroner_record", "government_archive") and "archive.org" not in url:
        return True
    host = site_of(url) + " " + url.lower()
    return any(p in host for p in ai_config.case_monitor.official_source_patterns)


def gate_solved(verdict: dict, min_confidence: float, min_sources: int) -> tuple[str, str]:
    """(status, reason). SOLVED only with confidence and independent or
    official support; otherwise STATUS_UNDER_REVIEW (a development the
    evidence does not yet carry)."""
    status = normalize_status(verdict.get("status"))
    if status != SOLVED:
        return status, verdict.get("reason") or ""
    conf = float(verdict.get("confidence") or 0)
    urls = [u for u in verdict.get("supporting_urls") or [] if isinstance(u, str) and u]
    sites = sorted({site_of(u) for u in urls if site_of(u)})
    official = [u for u in urls if is_official(u)]
    if conf < min_confidence:
        return UNDER_REVIEW, (f"verifier says solved but confidence {conf:.2f} < "
                              f"{min_confidence:.2f}")
    if len(sites) < min_sources and not official:
        return UNDER_REVIEW, (f"solved claim supported by {len(sites)} independent site(s) "
                              f"({', '.join(sites) or 'none'}); need {min_sources} or an "
                              "official source")
    support = (f"official source {site_of(official[0])}" if official
               else f"{len(sites)} independent sources ({', '.join(sites[:4])})")
    return SOLVED, f"{verdict.get('reason') or 'solved'} — {support}, confidence {conf:.2f}"


# ---------------------------------------------------------------------------
# status verifier (LLM role case_status_verifier)
# ---------------------------------------------------------------------------

VERIFIER_SYSTEM = """You decide whether a real criminal case is SOLVED or
UNSOLVED, using ONLY the provided search results / documents. Never use
outside knowledge; when the texts do not say, answer UNKNOWN.

SOLVED      the perpetrator is legally or officially established: a
            conviction, a guilty plea or confession accepted by a court,
            official closure naming the perpetrator (e.g. perpetrator dead
            and identified by the authorities), or for a disappearance:
            the person found AND the circumstances officially explained.
UNSOLVED    nobody charged or convicted; the investigation is open or cold.
STATUS_UNDER_REVIEW  a development without resolution: an arrest, a
            suspect named, charges filed but no verdict, remains identified
            but the perpetrator unknown, contradictory reports.
UNKNOWN     the texts do not show the state of the case.

An arrest alone is NOT solved. One vague article is not enough for
SOLVED: cite every URL that states the decisive fact.

Return JSON only:
{"status": "SOLVED|UNSOLVED|STATUS_UNDER_REVIEW|UNKNOWN",
 "confidence": 0.0,
 "solved_by": "conviction|confession|charges|official_closure|identification|none",
 "latest_development": "one sentence: the newest decisive development",
 "latest_development_date": "YYYY-MM-DD | YYYY-MM | YYYY | null",
 "incident_date": "YYYY-MM-DD | YYYY-MM | YYYY | null",
 "key_facts": [{"fact": "...", "url": "..."}],
 "supporting_urls": ["urls that state the decisive fact"],
 "reason": "why this status, in one or two sentences"}"""


async def verify_status(gen, case: dict, documents: list[dict]) -> dict:
    """case: {title, people, location}; documents: [{url, title, text}]."""
    user = json.dumps({"case": case, "documents": documents[:24]}, ensure_ascii=False)
    data, _ = await gen.generate_structured("case_status_verifier", VERIFIER_SYSTEM, user)
    data = data if isinstance(data, dict) else {}
    data["status"] = normalize_status(data.get("status"))
    try:
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0)))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    known = {d.get("url") for d in documents}
    # a cited URL must be one of the documents the verifier was shown
    data["supporting_urls"] = [u for u in data.get("supporting_urls") or [] if u in known]
    return data


def _mentions(raw: dict, text: str) -> bool:
    from app.lifecycle.identity import fold, tokens

    t = fold(text)
    names = [raw.get("title") or "", *(raw.get("key_people") or []), *(raw.get("aliases") or [])]
    for n in names:
        toks = tokens(n if isinstance(n, str) else "")
        if toks and sum(1 for w in toks if w in t) >= min(2, len(toks)):
            return True
    return False


async def _verify_one(raw: dict, found: list[dict], registry, gen) -> dict:
    people = [p for p in raw.get("key_people") or [] if isinstance(p, str)][:2]
    lang = (raw.get("languages_available") or ["en"])[0] or "en"
    queries = [f"{raw['title']} verdict sentenced", f"{(people or [raw['title']])[0]} arrested charged convicted"]
    docs: dict[str, dict] = {}
    for f in found:
        if _mentions(raw, f"{f.get('title')} {f.get('snippet')}") and f.get("url"):
            docs.setdefault(f["url"], {"url": f["url"], "title": f.get("title"),
                                       "text": f.get("snippet"), "date": f.get("published_at")})
    searches = 0
    for q in queries:
        try:
            res = await registry.search(q, lang, limit=8)
        except Exception as e:  # noqa: BLE001
            log.warning("status search failed: %s", e)
            res = []
        searches += 1
        for r in res:
            if r.url and _mentions(raw, f"{r.title} {r.snippet}"):
                docs.setdefault(r.url, {"url": r.url, "title": r.title, "text": r.snippet,
                                        "date": r.published_at})
    if not docs:
        return {"status": UNKNOWN, "confidence": 0.0, "reason": "no result names the case",
                "supporting_urls": [], "_searches": searches}
    verdict = await verify_status(gen, {"title": raw["title"], "people": people,
                                        "location": raw.get("location")}, list(docs.values()))
    verdict["_searches"] = searches
    verdict["_documents"] = len(docs)
    return verdict


# ---------------------------------------------------------------------------
# discovery side: duplicates -> verification -> ranking
# ---------------------------------------------------------------------------


def _claimed(raw: dict) -> str:
    return normalize_status(raw.get("resolution_status") or raw.get("status"))


def _recency(raw: dict) -> tuple[float, str]:
    incident = parse_date(raw.get("incident_date")) or parse_date(raw.get("approximate_date"))
    return case_recency(incident, parse_date(raw.get("latest_development_date")))


_EMPTY = {"", "unknown", "n/a", "na", "none", "null", "-", "?", "unbekannt"}


def _clean(raw: dict) -> dict:
    """Extraction placeholders ("UNKNOWN") are no data."""
    for k in ("location", "incident_date", "latest_development_date", "approximate_date",
              "resolution_evidence"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip().lower() in _EMPTY:
            raw[k] = None
    for k in ("aliases", "key_people", "source_urls", "identifiers"):
        if isinstance(raw.get(k), list):
            raw[k] = [x for x in raw[k] if not (isinstance(x, str) and x.strip().lower() in _EMPTY)]
    return raw


async def prepare_candidates(raw_candidates: list[dict], found: list[dict],
                             known: IdentityIndex, *, count: int, include_unsolved: bool,
                             registry=None, gen=None, progress=None) -> dict:
    """Duplicate check, status verification of the best, ranking. Returns
    {"candidates": [...], "stats": {...}}; every candidate carries
    `selection` = {state, duplicate, status, rank, recency, reason}."""
    cfg = ai_config.case_selection
    batch = IdentityIndex(list(known.items))
    fresh: list[dict] = []
    out: list[dict] = []
    for i, raw in enumerate(raw_candidates):
        if not isinstance(raw, dict) or not (raw.get("title") or "").strip():
            continue
        _clean(raw)
        ident = raw_identity(raw)
        verdict = batch.check(ident)
        raw["identity"] = ident.to_dict()
        if verdict.duplicate:
            raw["selection"] = {"state": "duplicate", "duplicate": verdict.to_dict(),
                                "reason": verdict.reason}
            out.append(raw)
            continue
        batch.add(Identity.from_dict({**ident.to_dict(), "kind": "batch", "id": i}))
        raw["selection"] = {"state": "eligible", "duplicate": verdict.to_dict()}
        fresh.append(raw)

    # verify the status of the most promising (by claimed status x recency)
    def pre(raw):
        return status_score(_claimed(raw), _recency(raw)[0])

    to_verify = [r for r in sorted(fresh, key=pre, reverse=True)
                 if include_unsolved or _claimed(r) != UNSOLVED][:cfg.verify_top_n]
    searches = 0
    if registry is not None and gen is not None and to_verify:
        import asyncio

        if progress:
            progress.stage("verifying_status")

        async def one(raw):
            try:
                return await _verify_one(raw, found, registry, gen)
            except Exception as e:  # noqa: BLE001
                log.warning("status verification failed for %s: %s", raw.get("title"), e)
                return {"status": UNKNOWN, "confidence": 0.0,
                        "reason": f"verification failed: {e}"[:200]}

        verdicts = await asyncio.gather(*(one(r) for r in to_verify))
        for raw, v in zip(to_verify, verdicts):
            searches += int(v.pop("_searches", 0) or 0)
            raw["verification"] = v

    for raw in fresh:
        v = raw.get("verification")
        if v:
            status, why = gate_solved(v, cfg.solved_min_confidence, 2)
            conf = v.get("confidence")
            # the verifier read more about the case than the discovery
            # snippets: its dates win when it has them
            for k in ("latest_development_date", "incident_date"):
                if v.get(k) and parse_date(str(v[k])):
                    raw[k] = str(v[k])
            evidence = v.get("latest_development") or why
            source = "verified"
        else:
            claimed = _claimed(raw)
            # an unverified "solved" claim is not enough to call it SOLVED
            status = UNDER_REVIEW if claimed == SOLVED else claimed
            conf, evidence = None, (raw.get("resolution_evidence") or "status not verified")
            why = "status claimed by the discovery extraction, not verified"
            source = "claimed"
        rec, rec_how = _recency(raw)
        score = status_score(status, rec)
        raw["selection"].update({"status": status, "status_confidence": conf,
                                 "status_reason": why, "status_source": source,
                                 "evidence": evidence, "rank": score, "recency": rec,
                                 "recency_basis": rec_how})
        if status == UNSOLVED and not include_unsolved:
            raw["selection"]["state"] = "filtered"
            raw["selection"]["reason"] = ("UNSOLVED — the standard pipeline suggests solved "
                                          "cases (include_unsolved to see it)")

    eligible = sorted([r for r in fresh if r["selection"]["state"] == "eligible"],
                      key=lambda r: r["selection"]["rank"], reverse=True)
    for pos, raw in enumerate(eligible):
        sel = raw["selection"]
        if pos < count:
            sel["state"] = "suggested"
            sel["reason"] = "; ".join(x for x in [
                f"rank {pos + 1} of {len(eligible)} (score {sel['rank']:.2f})",
                f"recent: {sel['recency_basis']} (recency {sel['recency']:.2f})",
                f"{sel['status']}" + (f" ({sel['status_confidence']:.2f})"
                                      if sel.get("status_confidence") is not None else "")
                + f" — {sel['evidence']}"[:300],
                "not used before: " + sel["duplicate"]["reason"],
            ] if x)
        else:
            sel["state"] = "not_shown"
            sel["reason"] = f"ranked {pos + 1}, below the top {count}"
    out.extend(fresh)
    stats = {"duplicates": sum(1 for r in out if r["selection"]["state"] == "duplicate"),
             "filtered_unsolved": sum(1 for r in out if r["selection"]["state"] == "filtered"),
             "verified": len(to_verify) if gen is not None else 0,
             "status_searches": searches,
             "checked_against": {"cases": known.count("case"),
                                 "suggestions": known.count("candidate")}}
    return {"candidates": out, "stats": stats}


# ---------------------------------------------------------------------------
# database side: store suggestions with their reasons
# ---------------------------------------------------------------------------


def _json(v) -> str:
    return json.dumps(v or [], ensure_ascii=False)


def ingest_candidates(db: Session, result: dict, *, count: int, include_unsolved: bool,
                      job_id: int | None = None) -> dict:
    """Store suggestions (and duplicate/filtered ones for the audit).
    Candidates the discovery job did not prepare (older providers, the
    fallback agent) are prepared here without the verifier."""
    from app.db.models import DiscoveryCandidate
    from app.services.research_jobs import _fingerprint

    raw_list = [r for r in result.get("candidates") or [] if isinstance(r, dict)]
    if raw_list and not all("selection" in r for r in raw_list):
        prepared = _prepare_sync(raw_list, IdentityIndex.from_db(db), count, include_unsolved)
        raw_list = prepared["candidates"]
        stats = prepared["stats"]
    else:
        stats = result.get("selection_stats") or {}

    # the database may have changed while the job ran: check again
    index = IdentityIndex.from_db(db)
    out, skipped = [], 0
    for raw in raw_list:
        sel = raw["selection"]
        if sel["state"] in ("suggested", "not_shown"):
            v = index.check(raw_identity(raw))
            if v.duplicate:
                sel.update({"state": "duplicate", "duplicate": v.to_dict(), "reason": v.reason})
        if sel["state"] == "not_shown":
            continue
        dup = sel.get("duplicate") or {}
        is_dup = sel["state"] == "duplicate"
        skipped += is_dup
        cand = DiscoveryCandidate(
            title=raw["title"].strip()[:500],
            query=((raw.get("suggested_queries") or [raw["title"]])[0] or raw["title"])[:500],
            rationale=(raw.get("rationale") or "")[:2000],
            fingerprint=_fingerprint(raw["title"], raw.get("key_people")),
            state=sel["state"], rejected=False, selected=False,
            resolution_status=sel.get("status") or normalize_status(raw.get("resolution_status")),
            resolution_confidence=sel.get("status_confidence"),
            resolution_evidence=(sel.get("evidence") or raw.get("resolution_evidence") or "")[:2000]
            or None,
            incident_date=(raw.get("incident_date") or raw.get("approximate_date") or None),
            latest_development_date=raw.get("latest_development_date") or None,
            location=(raw.get("location") or None),
            aliases_json=_json(raw.get("aliases")),
            people_json=_json(raw.get("key_people")),
            source_urls_json=_json(raw.get("source_urls")),
            recency_score=sel.get("recency"), rank_score=sel.get("rank"),
            suggestion_reason=sel.get("reason"),
            duplicate_of_case_id=dup.get("matched_id") if is_dup and dup.get("matched_kind") == "case" else None,
            duplicate_of_candidate_id=(dup.get("matched_id") if is_dup and dup.get("matched_kind")
                                       == "candidate" else None),
            duplicate_reason=sel.get("reason") if is_dup else None,
            discovery_job_id=job_id,
        )
        for k in ("incident_date", "latest_development_date"):
            if getattr(cand, k):
                setattr(cand, k, str(getattr(cand, k))[:40])
        if cand.location:
            cand.location = cand.location[:300]
        db.add(cand)
        db.flush()
        if sel["state"] == "suggested":
            index.add(candidate_identity(cand))
        out.append({**candidate_dict(cand), "narrative_potential": raw.get("narrative_potential"),
                    "languages_available": raw.get("languages_available") or [],
                    "source_richness": raw.get("source_richness"),
                    "angles": raw.get("angles") or [], "key_people": raw.get("key_people") or [],
                    "approximate_date": raw.get("approximate_date"),
                    "verification": raw.get("verification"),
                    "already_covered": is_dup})
    suggested = [c for c in out if c["state"] == "suggested"]
    suggested.sort(key=lambda c: c.get("rank_score") or 0, reverse=True)
    others = [c for c in out if c["state"] != "suggested"]
    return {"candidates": suggested, "rejected": others, "skipped_duplicates": skipped,
            "selection_stats": stats}


def _prepare_sync(raw_list, index, count, include_unsolved) -> dict:
    """prepare_candidates without network (no registry/gen): runs the
    coroutine to completion — it never awaits anything without them."""
    coro = prepare_candidates(raw_list, [], index, count=count,
                              include_unsolved=include_unsolved)
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("prepare_candidates awaited without a registry")


def candidate_dict(c) -> dict:
    return {
        "id": c.id, "candidate_id": c.id, "title": c.title, "query": c.query,
        "rationale": c.rationale, "state": c.state or "suggested",
        "selected": c.selected, "rejected": c.rejected, "case_id": c.case_id,
        "resolution_status": c.resolution_status or UNKNOWN,
        "resolution_confidence": c.resolution_confidence,
        "resolution_evidence": c.resolution_evidence,
        "incident_date": c.incident_date, "latest_development_date": c.latest_development_date,
        "location": c.location, "aliases": json.loads(c.aliases_json or "[]"),
        "people": json.loads(c.people_json or "[]"),
        "source_urls": json.loads(c.source_urls_json or "[]"),
        "recency_score": c.recency_score, "rank_score": c.rank_score,
        "suggestion_reason": c.suggestion_reason,
        "duplicate_of_case_id": c.duplicate_of_case_id,
        "duplicate_of_candidate_id": c.duplicate_of_candidate_id,
        "duplicate_reason": c.duplicate_reason,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }

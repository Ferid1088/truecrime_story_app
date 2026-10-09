"""Unsolved-case monitor — about twice a week, every UNSOLVED case.

Stage 1 FAST (every case, every run): a couple of searches limited to the
time since the last check, then deterministic signal detection (arrest,
suspect identified, remains identified, charges, confession, conviction,
official update, case closed, forensic breakthrough...). A result counts
only when it names the case. No signal -> the check is stored and the
case is done: no page fetch, no LLM.

Stage 2 DEEP (only after a signal): the signal pages plus a few targeted
searches are fetched and read, the `case_status_verifier` judges, and the
shared evidence gate decides: SOLVED needs confidence AND independent or
official sources; weaker evidence -> STATUS_UNDER_REVIEW. Every check
stores previous/current status, queries, signals, sources, confidence,
new facts and the reason. A covered case that goes UNSOLVED -> SOLVED
becomes a follow-up candidate (never a production by itself).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, CaseStatusCheck, MonitorRun, Source
from app.lifecycle import followups
from app.lifecycle.identity import case_identity, fold, tokens
from app.lifecycle.selection import gate_solved, parse_date, verify_status
from app.lifecycle.status import UNKNOWN, set_resolution
from app.utils import ensure_utc, utc_now

log = logging.getLogger(__name__)
_lock = asyncio.Lock()


def time_range_since(last) -> str:
    """SearXNG time range covering the time since the last check."""
    if last is None:
        return ai_config.case_monitor.first_check_time_range
    days = (utc_now() - ensure_utc(last)).total_seconds() / 86400
    for limit, name in ((1, "day"), (7, "week"), (31, "month")):
        if days <= limit:
            return name
    return "year"


def case_language(db: Session, case: Case) -> str:
    """The language the case's own sources are written in (local news
    reports a development first), else English."""
    langs = Counter(l for (l,) in db.query(Source.language).filter(Source.case_id == case.id)
                    if l and l != "unknown")
    for lang, _ in langs.most_common():
        if lang in ai_config.case_monitor.signal_terms:
            return lang
    return "en"


def names_for(db: Session | None, case: Case) -> dict:
    ident = case_identity(db, case)
    people = [p for p in ident.people if len(tokens(p)) >= 2]
    return {"title": case.canonical_title, "people": people[:4], "places": ident.places[:3],
            "aliases": ident.titles[1:4]}


def fast_queries(case: Case, names: dict) -> list[str]:
    primary = (names["people"] or [case.canonical_title])[0]
    place = (names["places"] or [""])[0]
    qs = [f"{primary} {place}".strip(), f"{case.canonical_title} news"]
    if names["aliases"]:
        qs.append(names["aliases"][0])
    out = []
    for q in qs:
        if q and q not in out:
            out.append(q)
    return out[: ai_config.case_monitor.fast_queries_per_case]


def names_case(names: dict, text: str) -> bool:
    """A result belongs to the case only when it names it: a person's
    full name, or the distinctive words of the title/an alias."""
    t = fold(text)
    for p in names["people"]:
        toks = tokens(p)
        if len(toks) >= 2 and all(w in t for w in toks):
            return True
    for title in [names["title"], *names["aliases"]]:
        toks = tokens(title)
        if toks and sum(1 for w in toks if w in t) >= min(2, len(toks)) and (
                len(toks) >= 2 or any(fold(p) in t for p in names["places"])):
            return True
    return False


def detect_signals(results: list[dict], names: dict, languages: list[str],
                   since=None) -> list[dict]:
    """Deterministic signal words in results that name the case."""
    terms = ai_config.case_monitor.signal_terms
    since_date = ensure_utc(since).date() if since else None
    out = []
    for r in results:
        text = f"{r.get('title') or ''} {r.get('snippet') or ''}"
        if not names_case(names, text):
            continue
        published = parse_date(r.get("published_at"))
        if since_date and published and published < since_date - timedelta(days=2):
            continue          # older than the last check: nothing new
        low = text.casefold()
        for lang in languages:
            for signal, words in (terms.get(lang) or {}).items():
                hit = next((w for w in words if w.casefold() in low), None)
                if hit:
                    out.append({"signal": signal, "term": hit, "url": r.get("url"),
                                "title": r.get("title"), "snippet": (r.get("snippet") or "")[:300],
                                "published_at": r.get("published_at")})
    seen, unique = set(), []
    for s in out:
        key = (s["signal"], s["url"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


class UnsolvedCaseMonitor:
    def __init__(self, provider=None, gen=None):
        self._provider = provider
        self._gen = gen

    @property
    def provider(self):
        if self._provider is None:
            from app.providers import get_research_provider

            self._provider = get_research_provider()
        return self._provider

    @property
    def gen(self):
        if self._gen is None:
            from app.providers.generation import get_generation_provider

            self._gen = get_generation_provider()
        return self._gen

    def due_cases(self, db: Session) -> list[Case]:
        return (db.query(Case)
                .filter(Case.resolution_status.in_(ai_config.case_monitor.statuses))
                .order_by(Case.id).all())

    async def run(self, db: Session, trigger: str = "scheduled") -> MonitorRun:
        async with _lock:
            run = MonitorRun(trigger=trigger, status="running")
            db.add(run)
            db.commit()
            try:
                for case in self.due_cases(db):
                    try:
                        check = await self.check_case(db, case, run)
                    except Exception as e:  # noqa: BLE001 — one case never stops the run
                        log.warning("monitor check of case %s failed: %s", case.id, e)
                        db.rollback()
                        check = CaseStatusCheck(
                            case_id=case.id, monitor_run_id=run.id, stage="fast",
                            outcome="error", previous_status=case.resolution_status,
                            current_status=case.resolution_status, reason=str(e)[:500])
                        db.add(check)
                        db.commit()
                    run.cases_checked += 1
                    run.deep_checks += check.stage == "deep"
                    run.status_changes += check.outcome == "confirmed_change"
                    run.search_calls += check.search_calls or 0
                    run.fetch_calls += check.fetch_calls or 0
                    run.llm_calls += check.llm_calls or 0
                    db.commit()
                run.status = "completed"
            except Exception as e:  # noqa: BLE001
                db.rollback()
                run.status = "failed"
                run.error = str(e)[:1000]
            run.finished_at = utc_now()
            db.commit()
            db.refresh(run)
            return run

    async def check_case(self, db: Session, case: Case, run: MonitorRun | None = None
                         ) -> CaseStatusCheck:
        cfg = ai_config.case_monitor
        # news since the monitor last looked at this case (first check:
        # the configured first range, no date cut)
        prev_check = (db.query(CaseStatusCheck).filter(CaseStatusCheck.case_id == case.id)
                      .order_by(CaseStatusCheck.id.desc()).first())
        last = prev_check.created_at if prev_check else None
        names = names_for(db, case)
        lang = case_language(db, case)
        languages = list(dict.fromkeys([lang, "en"]))
        time_range = time_range_since(last)
        queries = fast_queries(case, names)
        results: list[dict] = []
        searches = 0
        for q in queries:
            for ql in languages[:1]:
                try:
                    raw = await self.provider.registry.search(
                        q, ql, limit=cfg.fast_results_per_query, time_range=time_range)
                except Exception as e:  # noqa: BLE001
                    log.warning("monitor search failed: %s", e)
                    raw = []
                searches += 1
                results += [{"url": r.url, "title": r.title, "snippet": r.snippet,
                             "published_at": r.published_at} for r in raw]
        signals = detect_signals(results, names, languages, since=last)
        previous = case.resolution_status or UNKNOWN
        check = CaseStatusCheck(
            case_id=case.id, monitor_run_id=run.id if run else None, stage="fast",
            previous_status=previous, current_status=previous,
            queries_json=json.dumps([{"query": q, "language": languages[0],
                                      "time_range": time_range} for q in queries],
                                    ensure_ascii=False),
            signals_json=json.dumps(signals, ensure_ascii=False), search_calls=searches)
        if not signals:
            check.outcome = "no_signal"
            check.reason = (f"{len(results)} results in the last {time_range}; none names the case "
                            "with a development signal")
            case.resolution_checked_at = utc_now()
            db.add(check)
            db.commit()
            db.refresh(check)
            return check

        # ---- stage 2: deep verification -----------------------------------
        check.stage = "deep"
        check.outcome = "signal"
        db.add(check)
        db.commit()
        docs, fetches, extra = await self._read_sources(case, names, signals, languages)
        check.search_calls += extra
        check.fetch_calls = fetches
        verdict = await verify_status(
            self.gen, {"title": case.canonical_title, "people": names["people"],
                       "location": case.location, "previous_status": previous}, docs)
        check.llm_calls = 1
        status, why = gate_solved(verdict, cfg.solved_min_confidence,
                                  cfg.min_independent_sources)
        titles = {d["url"]: d.get("title") for d in docs}
        sources = [{"url": u, "title": titles.get(u)} for u in verdict.get("supporting_urls") or []]
        check.confidence = verdict.get("confidence")
        check.sources_json = json.dumps(sources, ensure_ascii=False)
        check.new_facts_json = json.dumps(verdict.get("key_facts") or [], ensure_ascii=False)
        development = verdict.get("latest_development") or ""
        if status in (UNKNOWN,) or status == previous:
            check.outcome = "not_confirmed" if status != previous else "unchanged"
            check.current_status = previous
            check.reason = (f"signals ({', '.join(sorted({s['signal'] for s in signals}))}) "
                            f"checked: {why or verdict.get('reason') or 'no change confirmed'}")
            case.resolution_checked_at = utc_now()
            db.commit()
            db.refresh(check)
            return check
        check.outcome = "confirmed_change"
        check.current_status = status
        check.reason = f"{previous} → {status}: {why}"
        db.commit()
        set_resolution(db, case, status, changed_by="monitor", reason=check.reason,
                       confidence=verdict.get("confidence"), sources=sources,
                       summary=development or None, check_id=check.id)
        if verdict.get("latest_development_date"):
            case.latest_development_date = str(verdict["latest_development_date"])[:40]
            db.commit()
        fu = followups.create_candidate(
            db, case, previous_status=previous, new_status=status, check=check,
            development=development, sources=sources, confidence=verdict.get("confidence"))
        if fu is not None and run is not None:
            run.follow_ups_created += 1
        db.refresh(check)
        return check

    async def _read_sources(self, case: Case, names: dict, signals: list[dict],
                            languages: list[str]) -> tuple[list[dict], int, int]:
        """Signal pages + a few targeted searches, fetched and extracted."""
        from app.research_engine.extractor import extract

        cfg = ai_config.case_monitor
        urls = list(dict.fromkeys(s["url"] for s in signals if s.get("url")))
        primary = (names["people"] or [case.canonical_title])[0]
        searches = 0
        for q in [f"{primary} convicted sentenced", f"{primary} police prosecutor statement"][
                : cfg.deep_extra_queries]:
            try:
                raw = await self.provider.registry.search(q, languages[0], limit=8)
            except Exception:  # noqa: BLE001
                raw = []
            searches += 1
            for r in raw:
                if r.url and names_case(names, f"{r.title} {r.snippet}") and r.url not in urls:
                    urls.append(r.url)
        docs: list[dict] = []
        fetches = 0
        for url in urls[: cfg.deep_max_pages]:
            fetches += 1
            text, title = None, None
            try:
                out = await self.provider.fetcher.fetch(url)
                if out.body:
                    doc = extract(out.body, out.content_type, url)
                    text, title = doc.text, doc.title
            except Exception as e:  # noqa: BLE001
                log.info("monitor fetch failed %s: %s", url, e)
            snip = next((s for s in signals if s.get("url") == url), {})
            docs.append({"url": url, "title": title or snip.get("title"),
                         "date": snip.get("published_at"),
                         "text": (text or snip.get("snippet") or "")[:6000]})
        return docs, fetches, searches


def check_dict(c: CaseStatusCheck) -> dict:
    return {
        "id": c.id, "case_id": c.case_id, "monitor_run_id": c.monitor_run_id, "stage": c.stage,
        "outcome": c.outcome, "previous_status": c.previous_status,
        "current_status": c.current_status, "confidence": c.confidence,
        "queries": json.loads(c.queries_json or "[]"), "signals": json.loads(c.signals_json or "[]"),
        "sources": json.loads(c.sources_json or "[]"),
        "new_facts": json.loads(c.new_facts_json or "[]"), "reason": c.reason,
        "search_calls": c.search_calls, "fetch_calls": c.fetch_calls, "llm_calls": c.llm_calls,
        "created_at": c.created_at,
    }


def run_dict(r: MonitorRun) -> dict:
    return {
        "id": r.id, "trigger": r.trigger, "status": r.status, "started_at": r.started_at,
        "finished_at": r.finished_at, "cases_checked": r.cases_checked,
        "deep_checks": r.deep_checks, "status_changes": r.status_changes,
        "follow_ups_created": r.follow_ups_created, "search_calls": r.search_calls,
        "fetch_calls": r.fetch_calls, "llm_calls": r.llm_calls, "error": r.error,
    }

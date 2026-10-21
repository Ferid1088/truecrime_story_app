"""Europeana and DPLA (P12 + structured dossier search): archive APIs for historical cases,
behind the same pre-filter and verification as every other provider. Both need a free key
(owner-registered); with no key configured the provider is skipped cleanly — one log line,
never a crash, never a silent failure that looks like "found nothing" for an unrelated reason.

Structured search (docs/open_sources_terms.md, read and live-probed 2026-10-21): instead of one
plain-text query per planner item, the providers build their own queries from the entity
dossier carried on the query dict (planner.query_item): one call per alias (capped), the
dossier's not-this/negative terms as API-side exclusions where the API supports them
(Europeana's NOT — DPLA's `q` has no negation, so there the verifier decides), and the
case/event year window padded by config as each API's date filter (`qf=YEAR:[a TO b]` /
`sourceResource.date.after|before`).

Rights: every item's own rights field is read and stored as the candidate's `license` (never
dropped silently, including "In Copyright" items — app.documentary.visuals.rights.classify
routes a europeana/dpla candidate through classify_open_data_rights, which keeps an unclear or
in-copyright item as editorial_review_required so a human sees and credits it, rather than
discarding it). Open-rights candidates come first: Europeana sends `reusability=open` on the
first pass and only repeats without it when the entity collected fewer open candidates than
`open_data.archives.open_min_candidates`; DPLA has no server-side rights filter, so its single
response is partitioned locally by the stored rights label under the same threshold rule.

Keys travel as URL parameters (wskey / api_key): they are never written to a log line, an
error message, a URL echoed anywhere or a report — every emitted string goes through _mask."""

from __future__ import annotations

import logging
import re
from collections import Counter

import httpx

from app.core.ai_config import DplaConfig, EuropeanaConfig, ai_config
from app.core.env import env_value
from app.documentary.visuals import rights as R
from app.documentary.visuals.research import Candidate

log = logging.getLogger(__name__)

# rights statuses that count as "open" for the open-first pass
_OPEN_STATUSES = ("public_domain", "creative_commons")

_KEY_PARAMS = ("wskey", "api_key")


def _mask(text: str, key: str | None) -> str:
    """The keys travel as URL parameters, so httpx error text contains them; nothing that
    reaches a log line or a report may contain the key."""
    return text.replace(key, "***") if key else text


def _terms(text: str) -> str:
    """One alias as bare ANDed search terms — deliberately not a quoted phrase: the catalog's
    inverted form ("Borden, Lizzie") would be missed by `"Lizzie Borden"` (probed live)."""
    return " ".join(re.sub(r"[^\w'\-\s]", " ", str(text)).split())


class _ArchiveProvider:
    """Shared structured search: dossier alias fan-out, era date filter, open-rights first,
    per-entity and per-case call caps. A provider instance lives for one research run, so the
    per-case counter covers the case's whole run."""

    name = ""
    label = ""  # display name for log lines ("Europeana", "DPLA")
    # set by the subclass __init__ (EuropeanaConfig | DplaConfig; both expose
    # search_url, api_key_env and credit_suffix)
    cfg: EuropeanaConfig | DplaConfig
    entity_types = ("place", "building", "landscape", "event", "object", "document", "vehicle",
                    "organization", "person")
    # True when the API can filter rights server-side on the request (Europeana reusability=open);
    # False when "open first" is a local filter on the one response (DPLA has no such parameter).
    server_rights_filter = False
    # True when the query language supports an exclusion operator (Europeana NOT).
    supports_not = False

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.arch = ai_config.visual_search.open_data_settings().archives
        # masked call trail for live reporting ("queries sent, status, count")
        self.calls_log: list[dict] = []
        self._calls_entity: Counter[str] = Counter()
        self._calls_case = 0

    # -- query building ------------------------------------------------------

    def _aliases(self, query: dict) -> list[str]:
        """One alias per API call (capped by config). A context query deliberately looks for
        a stand-in, so it is sent as its own text instead of being fanned out over aliases."""
        if (query.get("kind") or "exact") == "context":
            raw = _terms(query.get("query") or query.get("entity_name") or "")
            return [raw] if raw else []
        names = [query.get("entity_name"), *(query.get("aliases") or []),
                 *(query.get("must_name") or [])]
        seen: set[str] = set()
        out: list[str] = []
        for n in names:
            t = _terms(n or "")
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        if not out:
            t = _terms(query.get("query") or "")
            if t:
                out.append(t)
        return out[: self.arch.max_aliases_per_entity]

    def _negatives(self, query: dict) -> list[str]:
        """The dossier's known-wrong-subject terms as API-side exclusions, where the query
        language has a NOT operator (Europeana). Not applied to context queries: a stand-in
        has no "wrong subject" tied to the entity's identity."""
        if not self.supports_not or (query.get("kind") or "exact") == "context":
            return []
        seen: set[str] = set()
        out: list[str] = []
        for n in list(query.get("not_this") or []) + list(query.get("negative_terms") or []):
            t = _terms(n)
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out

    def _window(self, query: dict) -> tuple[int, int] | None:
        """The case/event year window padded by the configured before/after years."""
        w = query.get("date_window") or []
        if len(w) < 2 or w[0] is None:
            return None
        try:
            start, end = int(w[0]), int(w[1])
        except (TypeError, ValueError):
            return None
        return (start - self.arch.era_padding_before_years,
                end + self.arch.era_padding_after_years)

    # -- call accounting -----------------------------------------------------

    def _may_call(self, entity_key: str) -> bool:
        if (self._calls_case >= self.arch.max_calls_per_case
                or self._calls_entity[entity_key] >= self.arch.max_calls_per_entity):
            return False
        self._calls_case += 1
        self._calls_entity[entity_key] += 1
        return True

    async def _call(self, key: str, terms: str, negatives: list[str],
                    window: tuple[int, int] | None, limit: int, open_only: bool) -> list[Candidate]:
        params = self._params(key, terms, negatives, window, limit, open_only)
        shown = {k: v for k, v in params.items() if k not in _KEY_PARAMS}
        try:
            r = await self.client.get(self.cfg.search_url, params=params)
            status = r.status_code
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            self.calls_log.append({"alias": terms, "pass": "open" if open_only else "all",
                                   "status": "error", "candidates": 0, "params": shown})
            log.warning("%s search failed for %r: %s", self.label, terms,
                        _mask(str(e), key))
            return []
        cands = self._items(data, terms)
        self.calls_log.append({"alias": terms, "pass": "open" if open_only else "all",
                               "status": status, "candidates": len(cands), "params": shown})
        return cands

    # -- provider specifics (subclasses) --------------------------------------

    def _params(self, key: str, terms: str, negatives: list[str],
                window: tuple[int, int] | None, limit: int, open_only: bool) -> dict:
        raise NotImplementedError

    def _items(self, data: dict, found_for: str) -> list[Candidate]:
        raise NotImplementedError

    # -- the two passes --------------------------------------------------------

    async def search_for(self, query: dict, limit: int) -> list[Candidate]:
        key = env_value(self.cfg.api_key_env)
        if not key:
            log.info("%s skipped: %s is not configured (no key)", self.label,
                     self.cfg.api_key_env)
            return []
        aliases = self._aliases(query)
        if not aliases:
            return []
        entity_key = query.get("entity") or ""
        negatives = self._negatives(query)
        window = self._window(query)
        by_url: dict[str, Candidate] = {}

        if self.server_rights_filter:
            # pass 1: open rights only; pass 2 (all rights) only when pass 1 stayed
            # below the configured threshold for this entity
            for terms in aliases:
                if not self._may_call(entity_key):
                    break
                for c in await self._call(key, terms, negatives, window,
                                   limit, open_only=True):
                    by_url.setdefault(c.url, c)
            if len(by_url) < self.arch.open_min_candidates:
                for terms in aliases:
                    if not self._may_call(entity_key):
                        break
                    for c in await self._call(key, terms, negatives, window,
                                   limit, open_only=False):
                        by_url.setdefault(c.url, c)
        else:
            # no server-side rights filter: one call per alias, then the open share is
            # picked locally by the stored rights label under the same threshold rule
            for terms in aliases:
                if not self._may_call(entity_key):
                    break
                for c in await self._call(key, terms, negatives, window,
                                          limit, open_only=False):
                    by_url.setdefault(c.url, c)
            open_part = [c for c in by_url.values()
                         if R.classify_open_data_rights(c.license)[0] in _OPEN_STATUSES]
            if len(open_part) >= self.arch.open_min_candidates:
                by_url = {c.url: c for c in open_part}
        return list(by_url.values())[:limit]

    async def search(self, query: str, limit: int) -> list[Candidate]:
        """Plain-text entry point kept for direct calls: the same machinery with the text
        as its single 'alias' (no dossier fields, so no exclusions and no date window)."""
        return await self.search_for({"query": query}, limit)


class EuropeanaProvider(_ArchiveProvider):
    """Europeana Search API (api.europeana.eu/record/v2/search.json)."""

    name = "europeana"
    label = "Europeana"
    server_rights_filter = True
    supports_not = True

    def __init__(self, client: httpx.AsyncClient):
        super().__init__(client)
        self.cfg = ai_config.visual_search.open_data_settings().europeana

    def _params(self, key, terms, negatives, window, limit, open_only) -> dict:
        q = terms + "".join(f' NOT "{n}"' for n in negatives)
        qf = ["TYPE:IMAGE"]
        if window:
            qf.append(f"YEAR:[{window[0]} TO {window[1]}]")
        params: dict = {"wskey": key, "query": q, "rows": limit, "qf": qf}
        if open_only:
            params["reusability"] = "open"
        return params

    def _items(self, data: dict, found_for: str) -> list[Candidate]:
        out = []
        for item in data.get("items") or []:
            shown_by = (item.get("edmIsShownBy") or [None])[0]
            if not shown_by:
                continue
            shown_at = (item.get("edmIsShownAt") or item.get("guid") or [None])
            page = shown_at[0] if isinstance(shown_at, list) else shown_at
            title = (item.get("title") or [None])[0]
            provider_name = (item.get("dataProvider") or [None])[0]
            rights = (item.get("rights") or [None])[0]
            description = (item.get("dcDescription") or [None])
            caption = description[0] if description else None
            year = (item.get("year") or [None])[0]
            out.append(Candidate(
                url=shown_by, provider=self.name, page_url=page, title=title, caption=caption,
                license=rights, date=str(year) if year else None,
                credit=f"{provider_name} {self.cfg.credit_suffix}" if provider_name
                else self.cfg.credit_suffix,
                source_name="Europeana", found_for=found_for))
        return out


class DplaProvider(_ArchiveProvider):
    """DPLA API (api.dp.la/v2/items). Response shape live-confirmed 2026-10-21 with the
    project's own key; `q` has no negation and there is no server-side rights filter, so
    exclusions stay with the verifier and open-first is a local partition."""

    name = "dpla"
    label = "DPLA"
    server_rights_filter = False
    supports_not = False

    def __init__(self, client: httpx.AsyncClient):
        super().__init__(client)
        self.cfg = ai_config.visual_search.open_data_settings().dpla

    def _params(self, key, terms, negatives, window, limit, open_only) -> dict:
        params: dict = {"api_key": key, "q": terms, "page_size": limit}
        if window:
            params["sourceResource.date.after"] = str(window[0])
            params["sourceResource.date.before"] = str(window[1])
        return params

    def _items(self, data: dict, found_for: str) -> list[Candidate]:
        out = []
        for doc in data.get("docs") or []:
            obj = doc.get("object") or {}
            url = obj.get("@id") if isinstance(obj, dict) else obj
            if not url:
                continue
            shown_at = doc.get("isShownAt") or {}
            page = shown_at.get("@id") if isinstance(shown_at, dict) else shown_at
            source = doc.get("sourceResource") or {}
            title = (source.get("title") or [None])
            title = title[0] if isinstance(title, list) else title
            provider_obj = doc.get("provider") or {}
            provider_name = provider_obj.get("name") if isinstance(provider_obj, dict) else None
            data_provider = doc.get("dataProvider")
            rights = source.get("rights") or (
                obj.get("rights") if isinstance(obj, dict) else None) or (
                shown_at.get("rights") if isinstance(shown_at, dict) else None)
            if isinstance(rights, list):
                rights = rights[0] if rights else None
            # live fixture: date is a list of {begin, displayDate, end}; the documented
            # sample instead has a single object — both shapes occur
            dates = source.get("date")
            if isinstance(dates, dict):
                date = dates.get("displayDate") or dates.get("begin")
            elif isinstance(dates, list):
                date = (dates[0].get("displayDate") if isinstance(dates[0], dict)
                        else dates[0]) if dates else None
            else:
                date = dates
            credit_name = provider_name or (
                str(data_provider)[:120] if data_provider else None)
            out.append(Candidate(
                url=url, provider=self.name, page_url=page, title=title, license=rights,
                date=str(date) if date else None,
                credit=f"{credit_name} {self.cfg.credit_suffix}" if credit_name
                else self.cfg.credit_suffix,
                source_name="DPLA", found_for=found_for))
        return out

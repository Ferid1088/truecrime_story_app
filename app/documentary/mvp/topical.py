"""Topical fill: the owner's rule that a bare moment may show an approved picture or video that is at least half
related to what is said (the defendant while a court decision is told, another court picture for a court, the house or
the victim for a killing in a bed - never a police car for a court, never a map for a bed).

A word-overlap check cannot see that, so a text agent judges it from what each approved item was found to show. It sees
only descriptions (no image is sent), so the cost is a handful of calls for the whole film. The answers are kept on the
needs plan and reused until the approved catalog or a moment's words change. Placement, with the film's repetition
rules, is `direct.place_topical`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, VisualPlan
from app.documentary.mvp.moments import SAID_CHARS, Moment
from app.providers.generation import get_generation_provider
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

Match = tuple[str, float, str]          # item code, score, why
MATCH_TYPES = ("photo", "image", "video")


def catalog_of(items_by_beat: dict[str, list]) -> dict[str, dict]:
    """The approved pictures and videos (once each) as the matcher sees them. Maps and clips cut for a moment are
    not part of it: a map is a place, not a subject, and a clip is already bound to its own moment."""
    out: dict[str, dict] = {}
    for group in items_by_beat.values():
        for item in group:
            a = item.asset
            if item.moment or a.asset_type == "map" or item.code in out:
                continue
            out[item.code] = {"id": item.code, "kind": "video" if a.asset_type == "video" else "picture",
                              "title": (a.title or "")[:100], "depicts": (a.description or a.caption or "")[:240],
                              "entity": item.entity}
    return out


class TopicMatcher:
    def __init__(self, db: Session, case: Case, needs_plan: VisualPlan, matcher=None):
        self.db, self.case, self.plan = db, case, needs_plan
        self.cfg = ai_config.mvp().topical
        self._matcher = matcher
        self.calls = 0
        self.errors: list[str] = []

    @staticmethod
    def fingerprint(catalog: dict[str, dict]) -> str:
        text = json.dumps([[c["id"], c["title"], c["depicts"]] for c in catalog.values()], sort_keys=True)
        return hashlib.sha1(text.encode()).hexdigest()[:12]

    def _stored(self, fp: str) -> dict[str, list[list]]:
        topical = json.loads(self.plan.validation_json or "{}").get("mvp", {}).get("topical") or {}
        return dict(topical.get("matches") or {}) if topical.get("catalog") == fp else {}

    def _save(self, fp: str, matches: dict[str, list[list]]) -> None:
        validation = json.loads(self.plan.validation_json or "{}")
        validation.setdefault("mvp", {})["topical"] = {"catalog": fp, "matches": matches}
        self.plan.validation_json = json.dumps(validation, ensure_ascii=False)
        self.db.commit()

    async def ask(self, payload: dict) -> dict | None:
        if self._matcher is not None:
            return await self._matcher(payload)
        body = json.dumps(payload, ensure_ascii=False)
        gen = get_generation_provider()
        with track_run(self.db, self.case.id, "Topical fill: match", input_summary=f"{len(payload['moments'])} moments") as run:
            data, res = await asyncio.wait_for(run_agent("mvp.topic_match", gen, body),
                                               ai_config.mvp().call_timeout_s * 2)
            stamp_run(run, res, ai_config.agents["mvp.topic_match"].role)
        return data

    def valid(self, data: dict | None, batch: list[Moment], catalog: dict[str, dict]) -> dict[str, list[list]]:
        out: dict[str, list[list]] = {m.key: [] for m in batch}      # a moment the agent left out has no match
        known = {m.key for m in batch}
        for entry in (data or {}).get("matches") or []:
            if not isinstance(entry, dict) or entry.get("moment") not in known:
                continue
            rows = []
            for item in entry.get("items") or []:
                try:
                    score = float(item.get("score"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if item.get("id") in catalog and score >= self.cfg.min_score:
                    rows.append([item["id"], round(min(score, 1.0), 2), str(item.get("why") or "")[:200]])
            out[entry["moment"]] = sorted(rows, key=lambda r: -r[1])[:self.cfg.max_per_moment]
        return out

    async def run(self, gaps: list[dict], catalog: dict[str, dict]) -> dict[str, list[Match]]:
        """{moment key: [(item, score, why), ...]} for the bare moments; only scores of at least `min_score`."""
        if not self.cfg.enabled or not gaps or not catalog:
            return {}
        fp = self.fingerprint(catalog)
        matches = self._stored(fp)
        moments = {m.key: m for m in (Moment(g["beat"], g["start"], g["end"], g.get("said") or "")
                                      for g in gaps if g.get("said"))}
        todo = [m for k, m in moments.items() if k not in matches]
        listing = list(catalog.values())
        for i in range(0, len(todo), self.cfg.moments_per_call):
            batch = todo[i:i + self.cfg.moments_per_call]
            snap = BudgetService(self.db).snapshot(self.case.id)
            if snap.spent_eur >= snap.cap_eur - 1e-9:
                break
            payload = {"moments": [{"id": m.key, "beat": m.beat, "seconds": m.seconds, "said": m.said[:SAID_CHARS]}
                                   for m in batch],
                       "items": listing, "most_items_per_moment": self.cfg.max_per_moment}
            try:
                self.calls += 1
                matches.update(self.valid(await self.ask(payload), batch, catalog))
            except Exception as e:  # noqa: BLE001 - a failed batch is tried again next time, never remembered
                self.errors.append(f"{type(e).__name__}: {e}"[:200])
                log.warning("topical match failed: %s", e)
        self._save(fp, matches)
        return {k: [(c, s, w) for c, s, w in rows] for k, rows in matches.items() if k in moments and rows}

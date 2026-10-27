"""General imagery: pictures from the web for the moments that nothing of the case can cover.

Some words have a subject that can be shown without showing anybody: an insurance policy, a phone full of messages, a
laptop. For every moment that is still bare after the case's own pictures, footage, graphics and top-up, this module

  1. asks a text agent for a few web searches about the subject of the words (objects and documents, never a person),
  2. searches the web with the same Brave-based research the rest of the film uses,
  3. lets an independent vision check judge every new picture: it must show nobody's face, fit the words, carry no
     logo or caption and be sharp enough, and
  4. records the result as a requirement of the moment with one candidate per picture, so the topical fill, the review
     page and the verdict rules treat it like any other approved or doubtful picture.

A picture with a face in it is rejected whatever else it shows. A moment with no subject that can be shown without a
person stays bare (reported, never filled with a guess).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.concurrency import limit_size
from app.db.models import Case, MvpCandidate, MvpRequirement, VisualAsset
from app.documentary import storage
from app.documentary.mvp import verify as VF
from app.documentary.mvp.moments import SAID_CHARS, Moment
from app.documentary.mvp.overlays import data_url, jpeg_of
from app.documentary.mvp.research import query_item
from app.providers.generation import get_generation_provider
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

KEY_PREFIX = "GEN"
FUNCTION = "general"


def requirement_key(language: str, key: str) -> str:
    return f"{KEY_PREFIX}.{language}.{key}"


def entity_key(key: str) -> str:
    return f"general_{key}"


@dataclass
class Stats:
    moments: int = 0
    skipped_done: int = 0
    no_subject: int = 0
    searched: int = 0
    found: int = 0
    approved: int = 0
    needs_review: int = 0
    rejected: int = 0
    faces: int = 0
    stopped_for_budget: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {**self.__dict__, "errors": self.errors[:5]}


class GeneralImagery:
    def __init__(self, db: Session, case: Case, needs_plan_id: int, language: str, planner=None, searcher=None,
                 checker=None):
        self.db, self.case, self.plan_id, self.language = db, case, needs_plan_id, language
        self.cfg = ai_config.mvp().general
        self._planner, self._searcher, self._checker = planner, searcher, checker
        self.stats = Stats()

    def broke(self) -> bool:
        snap = BudgetService(self.db).snapshot(self.case.id)
        self.stats.stopped_for_budget = snap.spent_eur >= snap.cap_eur - 1e-9
        return self.stats.stopped_for_budget

    async def ask(self, agent: str, label: str, summary: str, call):
        with track_run(self.db, self.case.id, label, input_summary=summary) as run:
            data, res = await asyncio.wait_for(call(get_generation_provider()), ai_config.mvp().call_timeout_s)
            stamp_run(run, res, ai_config.agents[agent].role)
        return data

    def done(self, moment: Moment) -> bool:
        return self.db.query(MvpRequirement).filter_by(
            visual_plan_id=self.plan_id, requirement_key=requirement_key(self.language, moment.key)).first() is not None

    # -- what to search -----------------------------------------------------------
    def queries(self, data: dict | None, batch: list[Moment]) -> dict[str, list[str]]:
        known = {m.key for m in batch}
        out: dict[str, list[str]] = {}
        for entry in (data or {}).get("moments") or []:
            if not isinstance(entry, dict) or entry.get("id") not in known:
                continue
            found: list[str] = []
            for q in entry.get("queries") or []:
                text = " ".join(str(q or "").split())[:self.cfg.max_query_chars]
                if text and text not in found:
                    found.append(text)
            out[entry["id"]] = found[:self.cfg.queries_per_moment]
        return out

    async def plan(self, batch: list[Moment]) -> dict[str, list[str]]:
        payload = {"moments": [{"id": m.key, "said": m.said[:SAID_CHARS], "seconds": m.seconds} for m in batch],
                   "queries_per_moment": self.cfg.queries_per_moment}
        if self._planner is not None:
            return self.queries(await self._planner(payload), batch)
        body = json.dumps(payload, ensure_ascii=False)
        data = await self.ask("mvp.general_queries", "General imagery: searches", f"{len(batch)} moments",
                              lambda gen: run_agent("mvp.general_queries", gen, body))
        return self.queries(data, batch)

    async def search(self, wanted: dict[str, list[str]]) -> list[VisualAsset]:
        # an "exact" search, because a context search skips Brave (its pictures carry no known rights); nothing is
        # named, so the name pre-filter is off and the checker alone judges what the picture is about
        items = [{**query_item({"key": entity_key(key), "name": q, "type": "object", "footage": False}, q),
                  "must_name": []} for key, queries in wanted.items() for q in queries]
        if not items:
            return []
        self.stats.searched += len(items)
        if self._searcher is not None:
            await self._searcher(items)
        else:
            from app.agents.visual_research import VisualResearchAgent

            await VisualResearchAgent().run(self.db, self.case, items)
        keys = {entity_key(k) for k in wanted}
        found = [a for a in self.db.query(VisualAsset).filter(
            VisualAsset.case_id == self.case.id, VisualAsset.entity_key.in_(keys),
            VisualAsset.asset_type == "photo", VisualAsset.local_path.isnot(None),
            VisualAsset.file_removed_at.is_(None)).all()]
        log.info("general imagery: %d searches, %d pictures", len(items), len(found))
        return found

    # -- the verdict ----------------------------------------------------------------
    async def check(self, moment: Moment, asset: VisualAsset) -> dict:
        path = storage.resolve(asset.local_path)
        if path is None or not path.exists():
            raise FileNotFoundError(asset.local_path)
        payload = {"said": moment.said[:SAID_CHARS], "title": asset.title, "found_for": asset.found_for}
        images = [data_url(await asyncio.to_thread(jpeg_of, path, self.cfg.photo_width))]
        if self._checker is not None:
            return await self._checker(payload, images) or {}
        body = json.dumps(payload, ensure_ascii=False)
        return await self.ask("mvp.general_check", "General imagery: check", asset.asset_code,
                              lambda gen: run_agent("mvp.general_check", gen, body, images=images)) or {}

    def decide(self, verdict: dict) -> tuple[str, list[str]]:
        """approved / needs_review / rejected, and why. A visible face is never accepted, and a checker that does not
        say whether there is one has not looked."""
        if verdict.get("faces") is not False:
            return "rejected", ["a person's face is visible (or the checker did not say there is none)"]
        if verdict.get("unusable"):
            return "rejected", ["the picture carries a logo, caption, advertisement or watermark"]
        fit = str(verdict.get("fit") or "no").lower()
        if fit not in {*self.cfg.accept_fit, *self.cfg.review_fit}:
            return "rejected", [f"it does not fit the words ({verdict.get('why') or fit})"]
        try:
            quality = float(verdict.get("quality"))
        except (TypeError, ValueError):
            quality = 0.0
        if quality < ai_config.mvp().verification.min_quality:
            return "rejected", ["too poor to hold on screen"]
        reasons = []
        if verdict.get("sensitive"):
            reasons.append("the picture is sensitive and needs the owner's decision")
        if fit in self.cfg.review_fit:
            reasons.append("only loosely related to the words")
        return ("needs_review", reasons) if reasons else ("approved", [])

    def record(self, moment: Moment, judged: list[tuple[VisualAsset, str, list[str], dict]], outcome: str) -> None:
        key = requirement_key(self.language, moment.key)
        row = self.db.query(MvpRequirement).filter_by(visual_plan_id=self.plan_id, requirement_key=key).first()
        spec = {"need": moment.said[:200], "acceptable_roles": ["illustration"],
                "general": {"key": moment.key, "language": self.language, "beat": moment.beat,
                            "said": moment.said[:SAID_CHARS], "seconds": moment.seconds, "outcome": outcome}}
        if row is None:
            row = MvpRequirement(case_id=self.case.id, visual_plan_id=self.plan_id, requirement_key=key,
                                 beat_id=moment.beat, function=FUNCTION, entity_key=None,
                                 role_needed="illustration", priority="low")
            self.db.add(row)
        row.spec_json = json.dumps(spec, ensure_ascii=False)
        row.status = "met" if any(j[1] == "approved" for j in judged) else "open"
        row.shortage_json = None if row.status == "met" else json.dumps({"reason": outcome})
        self.db.flush()
        for asset, status, reasons, verdict in judged:
            asset.description = str(verdict.get("depicts") or asset.description or "")[:1000]
            asset.verification_status = "verified" if status == "approved" else (
                "rejected" if status == "rejected" else "needs_review")
            cand = self.db.query(MvpCandidate).filter_by(requirement_id=row.id, visual_asset_id=asset.id).first()
            if cand is None:
                cand = MvpCandidate(requirement_id=row.id, visual_asset_id=asset.id)
                self.db.add(cand)
            prov, _ = VF.provenance_decision(asset)
            rights, _ = VF.rights_decision(asset)
            cand.role = "illustration"
            cand.provenance_status, cand.rights_status = prov, rights
            cand.identity_status = "needs_review" if status == "needs_review" else "approved"
            cand.ethics_status = "needs_review" if verdict.get("sensitive") else "approved"
            cand.scene_status = "rejected" if status == "rejected" else "approved"
            cand.reasons_json = json.dumps(reasons, ensure_ascii=False)
            cand.verifiers_json = json.dumps([{"verifier": "mvp.general_check", "fit": verdict.get("fit"),
                                               "faces": verdict.get("faces"), "why": verdict.get("why")}],
                                             ensure_ascii=False)
            cand.final_status = VF.final_status(cand)
        self.db.commit()

    async def judge(self, moment: Moment, assets: list[VisualAsset], gate: asyncio.Semaphore) -> None:
        judged = []
        for asset in assets:
            async with gate:
                if self.broke():
                    return
                try:
                    verdict = await self.check(moment, asset)
                except Exception as e:  # noqa: BLE001 - one unreadable picture never stops the others
                    self.stats.errors.append(f"check: {type(e).__name__}: {e}"[:200])
                    continue
            status, reasons = self.decide(verdict)
            self.stats.faces += verdict.get("faces") is not False
            setattr(self.stats, status, getattr(self.stats, status) + 1)
            judged.append((asset, status, reasons, verdict))
        self.record(moment, judged, "searched the web for the subject of these words")

    # -- all moments ------------------------------------------------------------------
    async def run(self, gaps: list[dict]) -> dict:
        """Search for the bare moments `gaps` (the plan's report), longest first."""
        if not self.cfg.enabled or not gaps:
            return self.stats.as_dict()
        moments = sorted((Moment(g["beat"], g["start"], g["end"], g.get("said") or "") for g in gaps if g.get("said")),
                         key=lambda m: -m.seconds)[:self.cfg.max_moments]
        self.stats.moments = len(moments)
        todo = [m for m in moments if not self.done(m)]
        self.stats.skipped_done = len(moments) - len(todo)
        gate = asyncio.Semaphore(limit_size("vision"))
        for i in range(0, len(todo), self.cfg.moments_per_call):
            batch = todo[i:i + self.cfg.moments_per_call]
            if self.broke():
                break
            try:
                wanted = self.queries_for(await self.plan(batch), batch)
                found = await self.search(wanted)
            except Exception as e:  # noqa: BLE001 - a failed batch is tried again next run, never remembered
                self.stats.errors.append(f"search: {type(e).__name__}: {e}"[:200])
                log.warning("general imagery failed: %s", e)
                continue
            self.stats.found += len(found)
            by_moment = {m.key: [a for a in found if a.entity_key == entity_key(m.key)] for m in batch}
            for m in batch:
                if m.key not in wanted:
                    self.stats.no_subject += 1
                    self.record(m, [], "the words have no subject that can be shown without a person")
            await asyncio.gather(*(self.judge(m, by_moment[m.key], gate) for m in batch if m.key in wanted))
        return self.stats.as_dict()

    @staticmethod
    def queries_for(planned: dict[str, list[str]], batch: list[Moment]) -> dict[str, list[str]]:
        return {m.key: planned[m.key] for m in batch if planned.get(m.key)}

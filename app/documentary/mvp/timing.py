"""When in the story a picture or clip may first be shown. A model reads what each approved picture or clip shows (its
description) next to the story's beats and names the phase of the story it belongs to (scene, investigation,
interview, arrest or charge, court filing, trial, verdict, sentencing, aftermath, background) and the first beat that
tells of it. The film shows the asset from that beat on, never before: a criminal complaint is not laid over the
search of the garage. The label is kept on the asset (`spec_json.timing`) and read by the planner and the script."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.providers.generation import get_generation_provider
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)
ANY = "any"
DESCRIPTION_CHARS = 420


@dataclass(frozen=True)
class Timing:
    phase: str
    not_before: str
    why: str

    def as_json(self, sha256: str | None) -> dict:
        return {"version": ai_config.mvp().timing.version, "sha256": sha256, "phase": self.phase,
                "not_before": self.not_before, "why": self.why}


def stored(asset: VisualAsset) -> Timing | None:
    """The label of this asset when this version of the classifier made it for this file."""
    raw = json.loads(asset.spec_json or "{}").get("timing")
    if not raw or raw.get("version") != ai_config.mvp().timing.version or raw.get("sha256") != asset.sha256:
        return None
    return Timing(raw["phase"], raw["not_before"], raw.get("why", ""))


def blocks(asset: VisualAsset | None, order: dict[str, int] | list[str], beat_id: str | None) -> bool:
    """True when the story has not told of what this asset shows by `beat_id`."""
    if asset is None or beat_id is None:
        return False
    label = stored(asset)
    if label is None or label.not_before == ANY:
        return False
    rank = order if isinstance(order, dict) else {b: i for i, b in enumerate(order)}
    return beat_id in rank and label.not_before in rank and rank[beat_id] < rank[label.not_before]


def valid(entry: dict, beats: set[str]) -> Timing | None:
    """The model's label when it follows the rules: a known phase, a known beat (or `any` only for a phase that does
    not depend on a moment of the story)."""
    cfg = ai_config.mvp().timing
    phase, beat = str(entry.get("phase") or ""), str(entry.get("not_before") or "")
    if phase not in cfg.phases:
        return None
    if beat == ANY:
        if phase not in cfg.always_ok:
            return None
    elif beat not in beats:
        return None
    return Timing(phase, beat, " ".join(str(entry.get("why") or "").split())[:200])


def describe(asset: VisualAsset) -> dict:
    ver = json.loads(asset.verification_json or "{}") if asset.verification_json else {}
    return {"asset": asset.asset_code, "type": asset.asset_type,
            "title": (asset.title or "")[:160], "source": (asset.source_name or "")[:80],
            "description": " ".join(str(x) for x in (asset.description, ver.get("depicts")) if x)[:DESCRIPTION_CHARS],
            **({"owner_note": asset.owner_note} if asset.owner_note else {})}


class TimingClassifier:
    def __init__(self, db: Session, case: Case, blueprint: dict, asker=None):
        self.db, self.case, self._asker = db, case, asker
        self.cfg = ai_config.mvp().timing
        self.beats = [{"id": b["id"], "summary": " ".join(str(b.get("summary") or "").split())[:self.cfg.summary_chars]}
                      for b in blueprint.get("beats") or [] if b.get("id")]
        self.calls = 0
        self.errors: list[str] = []
        self.stopped_for_budget = False

    async def ask(self, assets: list[VisualAsset]) -> dict:
        payload = {"beats": self.beats, "phases": self.cfg.phases, "assets": [describe(a) for a in assets]}
        if self._asker is not None:
            return await self._asker(payload) or {}
        gen = get_generation_provider()
        with track_run(self.db, self.case.id, "Story timing", input_summary=f"{len(assets)} assets") as run:
            data, res = await asyncio.wait_for(
                run_agent("mvp.timing", gen, json.dumps(payload, ensure_ascii=False)),
                ai_config.mvp().call_timeout_s * 2)
            stamp_run(run, res, ai_config.agents["mvp.timing"].role)
        self.calls += 1
        return data or {}

    async def run(self, assets: list[VisualAsset]) -> dict:
        """Label every asset that has no current label; an asset the model could not label stays unlabelled (and
        is asked again next time)."""
        todo = [a for a in assets if stored(a) is None]
        out = {"labelled": 0, "kept": len(assets) - len(todo), "failed": 0}
        known = {b["id"] for b in self.beats}
        for start in range(0, len(todo), self.cfg.assets_per_call):
            batch = todo[start:start + self.cfg.assets_per_call]
            snap = BudgetService(self.db).snapshot(self.case.id)
            if snap.spent_eur >= snap.cap_eur - 1e-9:
                self.stopped_for_budget = True
                out["failed"] += len(todo) - start
                break
            try:
                answer = await self.ask(batch)
            except Exception as e:  # noqa: BLE001 - a failed call is retried next run, never taken as "any time"
                self.errors.append(str(e)[:200])
                log.warning("story timing call failed: %s", e)
                out["failed"] += len(batch)
                continue
            by_code = {a.asset_code: a for a in batch}
            for entry in answer.get("assets") or []:
                asset = by_code.get(entry.get("asset")) if isinstance(entry, dict) else None
                label = valid(entry, known) if asset is not None else None
                if label is None:
                    continue
                spec = json.loads(asset.spec_json or "{}")
                spec["timing"] = label.as_json(asset.sha256)
                asset.spec_json = json.dumps(spec, ensure_ascii=False)
                out["labelled"] += 1
            self.db.commit()
        out["failed"] = len([a for a in todo if stored(a) is None])
        return out

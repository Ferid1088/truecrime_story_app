"""The VisualDirector agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.visuals.director."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from collections import Counter
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import AudioPlan, Case, EditorialBlueprint, VisualAsset, VisualPlan
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.visuals.director import (
    ASSET_COMMANDS,
    DIRECTOR_SYSTEM,
    DIRECT_CHUNK,
    OPENING_VISUALS,
    _attach_timeline,
    _beat_view,
    _loads,
    add_found_candidates,
    assign_motion,
    beat_candidates,
    beat_marks,
    ensure_found_used,
    fill_candidates,
    max_shots,
    opening_beats,
    validate_visual_plan,
)


class VisualDirector:
    def __init__(self):
        self.gen = get_generation_provider()

    def candidates(self, db: Session, case: Case, blueprint: dict, requirements: dict,
                   profile: str | None = None, marks: dict | None = None
                   ) -> dict[str, list[tuple[float, VisualAsset]]]:
        return beat_candidates(db, case, blueprint, requirements, profile, marks)

    def _context(self, db: Session, case: Case, plan_row: VisualPlan,
                 blueprint_row: EditorialBlueprint, audio_plan: AudioPlan | None,
                 profile: str | None) -> dict:
        from app.documentary.openings import opening_of_blueprint

        blueprint = json.loads(blueprint_row.blueprint_json or "{}")
        requirements = json.loads(plan_row.requirements_json or "{}")
        _attach_timeline(db, blueprint_row, blueprint, requirements)
        ap = json.loads(audio_plan.plan_json) if audio_plan else {}
        marks = beat_marks(db, blueprint_row, blueprint)
        opening = opening_of_blueprint(db, blueprint_row)
        strategy = opening.get("strategy")
        assets = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
        return {
            "assets": {a.asset_code: a for a in assets},
            "blueprint": blueprint, "requirements": requirements, "ap": ap,
            "after": {pb["beat_id"]: (pb.get("after") or {}).get("type")
                      for pb in ap.get("beats") or []},
            "marks": marks,
            "cands": self.candidates(db, case, blueprint, requirements, profile, marks),
            "reqs": {b["beat_id"]: b for b in requirements.get("beats") or []},
            "entities": {e["key"]: e for e in requirements.get("entities") or []},
            "opening": {"strategy": strategy, "reason": opening.get("reason"),
                        "guidance": OPENING_VISUALS.get(strategy or "")},
            "openers": opening_beats(blueprint),
            "story_reveals": [
                {"beat_id": b["id"], "purpose": b.get("purpose"),
                 "reveals": str(b.get("summary") or "")[:200]}
                for b in blueprint.get("beats") or []
                if b.get("purpose") in set(ai_config.attention.firewall_purposes)],
            "case_status": getattr(case, "resolution_status", None) or "UNKNOWN",
        }

    async def _direct(self, db: Session, case: Case, ctx: dict, part: list[dict],
                      prev: dict | None, used: Counter, mapped: list[str],
                      searched: dict[str, list[dict]] | None = None) -> tuple[list[dict], str | None]:
        payload = {
            "case": case.canonical_title,
            "case_status": ctx["case_status"],
            "opening": ctx["opening"],
            "places_already_mapped": mapped,
            # what the film reveals where (no picture may give it away earlier)
            "story_reveals": ctx.get("story_reveals") or [],
            "previous_beat": ({"beat_id": prev["id"], "summary": prev.get("summary"),
                               "visual_intent": prev.get("visual_intent")} if prev else None),
            "beats": [_beat_view(b, ctx["reqs"].get(b["id"], {}), ctx["cands"].get(b["id"], []),
                                 ctx["after"].get(b["id"]), ctx["marks"].get(b["id"]),
                                 ctx["entities"], used,
                                 ctx["opening"] if b["id"] in ctx["openers"] else None,
                                 (searched or {}).get(b["id"]))
                      for b in part],
        }
        with track_run(db, case.id, "Visual Director",
                       input_summary=f"beats {part[0]['id']}–{part[-1]['id']}") as run:
            raw, res = await run_agent("visuals.direct", self.gen, json.dumps(payload, ensure_ascii=False), system=DIRECTOR_SYSTEM)
            stamp_run(run, res, "visual_director")
        beats = [b for b in (raw or {}).get("beats") or [] if isinstance(b, dict)]
        return beats, getattr(res, "model", None)

    @staticmethod
    def _count(raw_beats: list[dict], reqs: dict, used: Counter, mapped: list[str]) -> None:
        """What the film already shows after these beats (for the next chunk)."""
        for b in raw_beats:
            for s in b.get("shots") or []:
                if not isinstance(s, dict):
                    continue
                cmd = str(s.get("command") or "").upper()
                if cmd in ASSET_COMMANDS and s.get("asset_id"):
                    used[str(s["asset_id"])] += 1
                if cmd == "SHOW_MAP":
                    place = s.get("place") or reqs.get(str(b.get("beat_id")), {}).get("map_place")
                    if place and place not in mapped:
                        mapped.append(place)

    def _finish(self, ctx: dict, plan: dict, plan_row: VisualPlan, report: dict,
                key: str = "plan") -> None:
        from app.documentary.visuals.generated import attach_overlays

        plan = assign_motion(plan, ctx["blueprint"], ctx["ap"], ctx["assets"])
        plan = attach_overlays(plan, ctx["requirements"])
        plan["candidates"] = fill_candidates(ctx["cands"])
        plan["opening"] = {k: ctx["opening"].get(k) for k in ("strategy", "reason")}
        plan["case_status"] = ctx["case_status"]
        validation = _loads(plan_row.validation_json, {})
        validation[key] = report
        plan_row.plan_json = json.dumps(plan, ensure_ascii=False)
        plan_row.validation_json = json.dumps(validation, ensure_ascii=False)

    async def create(self, db: Session, case: Case, plan_row: VisualPlan,
                     blueprint_row: EditorialBlueprint, audio_plan: AudioPlan | None,
                     chunk: int = DIRECT_CHUNK, profile: str | None = None) -> VisualPlan:
        ctx = self._context(db, case, plan_row, blueprint_row, audio_plan, profile)
        beats = ctx["blueprint"].get("beats") or []
        used: Counter = Counter()
        mapped: list[str] = []
        raw_beats, model = [], None
        # in film order: each chunk knows what the film already shows
        for i in range(0, len(beats), max(chunk, 1)):
            part = beats[i:i + chunk]
            got, m = await self._direct(db, case, ctx, part, beats[i - 1] if i else None,
                                        used, mapped)
            model = m or model
            self._count(got, ctx["reqs"], used, mapped)
            raw_beats += got
        plan, report = validate_visual_plan(
            {"beats": raw_beats}, ctx["blueprint"], ctx["requirements"],
            {bid: [a.asset_code for _, a in lst] for bid, lst in ctx["cands"].items()},
            ctx["assets"], ctx["marks"], opening=ctx["opening"])
        self._finish(ctx, plan, plan_row, report)
        plan_row.status = "planned"
        plan_row.generation_model = model
        db.commit()
        return plan_row

    async def redirect(self, db: Session, case: Case, plan_row: VisualPlan,
                       blueprint_row: EditorialBlueprint, audio_plan: AudioPlan | None,
                       beat_ids: list[str], profile: str | None = None,
                       searched: dict[str, list[dict]] | None = None) -> dict:
        """Direct only `beat_ids` again with the library as it is now (after
        a production search); every other beat stays exactly as planned
        and still counts for the film-level rules. `searched`: per beat
        what was searched for which sentence and what was found."""
        ctx = self._context(db, case, plan_row, blueprint_row, audio_plan, profile)
        add_found_candidates(ctx["cands"], searched or {}, ctx["assets"], ctx["blueprint"],
                             profile)
        old = _loads(plan_row.plan_json, {})
        old_beats = {pb["beat_id"]: pb for pb in old.get("beats") or []}
        beats = [b for b in ctx["blueprint"].get("beats") or [] if b["id"] in set(beat_ids)]
        if not beats:
            return {"redirected": []}
        keep = {bid: pb for bid, pb in old_beats.items() if bid not in set(beat_ids)}
        used: Counter = Counter()
        mapped: list[str] = []
        self._count(list(keep.values()), ctx["reqs"], used, mapped)
        for pb in keep.values():  # validated shots name their map place
            for s in pb.get("shots") or []:
                if s.get("command") == "SHOW_MAP" and s.get("map_place") \
                        and s["map_place"] not in mapped:
                    mapped.append(s["map_place"])
        order = [b["id"] for b in ctx["blueprint"].get("beats") or []]
        prev = None
        first = order.index(beats[0]["id"])
        if first:
            prev = ctx["blueprint"]["beats"][first - 1]
        raw_beats, model = await self._direct(db, case, ctx, beats, prev, used, mapped, searched)
        assets_by_code = ctx["assets"]
        cand_codes = {bid: [a.asset_code for _, a in lst] for bid, lst in ctx["cands"].items()}
        limits = {b["id"]: max_shots(b, len(ctx["marks"].get(b["id"]) or []) or None)
                  for b in beats}
        raw_beats = ensure_found_used(raw_beats, searched or {}, cand_codes, assets_by_code,
                                      limits)
        # beats the model left out keep their earlier shots
        missing = {b["id"] for b in beats} - {str(r.get("beat_id")) for r in raw_beats}
        for bid in missing:
            if bid in old_beats:
                keep[bid] = old_beats[bid]
        plan, report = validate_visual_plan(
            {"beats": raw_beats}, ctx["blueprint"], ctx["requirements"], cand_codes,
            assets_by_code, ctx["marks"], opening=ctx["opening"], keep=keep)
        redone = {b["id"] for b in beats} - missing
        plan["search_requests"] = [r for r in old.get("search_requests") or []
                                   if r.get("beat_id") not in redone] + plan["search_requests"]
        if old.get("texts"):
            plan["texts"] = old["texts"]
        self._finish(ctx, plan, plan_row, report, key="redirect")
        if model:
            plan_row.generation_model = model
        db.commit()
        return {"redirected": sorted(redone, key=order.index), "report": report}

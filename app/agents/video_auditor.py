"""The VideoAuditor agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.visuals.video_auditor."""

from __future__ import annotations

from app.agents.runner import run_agent
import asyncio
import json
from sqlalchemy.orm import Session
from app.db.models import Case, VisualAsset
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T
from app.documentary.visuals.pieces import piece_frames
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.visuals.video_auditor import (
    DESCRIBE_SYSTEM,
    PLACEMENT_SYSTEM,
    _limits,
    _piece_times,
    _source_length,
    _urls,
    describe_decide,
    piece_parent,
)


class VideoAuditor:
    def __init__(self, gen=None, frames=None):
        self.gen = gen or get_generation_provider()
        self._frames = frames or piece_frames

    async def describe(self, db: Session, case: Case, asset: VisualAsset,
                       entities: list[dict], facts: list[dict]) -> VisualAsset:
        """Check a piece — the cut, the name and description, the content —
        and record the verdict as its verification. A wrong cut is moved to
        the auditor's better start/end, a wrong description replaced by the
        auditor's; each correction is checked again (at most
        documentary.max_redos times). Still not right → rejected."""
        from app.core.ai_config import ai_config

        if not asset.thumbnail_path:  # the library's still of the clip
            from app.documentary.visuals.verification import _keyframe_thumb

            _keyframe_thumb(asset)
        previous = json.loads(asset.verification_json or "{}") if asset.verification_json else {}
        provisional = previous.get("provisional_tier") or asset.relevance_tier
        history: list[dict] = []
        redos = ai_config.documentary.max_redos
        while True:
            v, res, n_frames = await self._check_once(db, case, asset, entities, facts)
            cut_ok = v.get("cut_ok") is not False
            desc_ok = v.get("description_ok") is not False
            history.append({"window": [asset.clip_start, asset.clip_end], "cut_ok": cut_ok,
                            "description_ok": desc_ok,
                            "reasons": list(v.get("reasons") or [])[:4]})
            if (cut_ok and desc_ok) or len(history) > redos:
                break
            changed = False
            if not cut_ok and self._move_cut(asset, v):
                changed = True
            if not desc_ok and str(v.get("name") or "").strip() and \
                    str(v.get("description") or "").strip():
                asset.title = str(v["name"]).strip()[:500]
                asset.description = str(v["description"]).strip()[:1000]
                changed = True
            if not changed:
                break
            db.commit()
        status, conf, reason = describe_decide(v)
        if status != "rejected" and not (cut_ok and desc_ok):
            status, reason = "rejected", ("cut_not_meaningful" if not cut_ok
                                          else "description_wrong")
        tier, tier_reason = T.verified_why(asset, {**v, "depicts": v.get("description")},
                                           provisional)
        asset.verification_status = status
        asset.verification_confidence = round(conf, 3)
        asset.relevance_tier = tier
        asset.case_relevance = T.tier_label(tier)
        if not piece_parent(asset):  # a whole clip: the auditor names it
            name = str(v.get("name") or "").strip()
            if name:
                asset.title = name[:500]
            asset.description = (str(v.get("description") or asset.description or "")[:1000]
                                 or None)
        if v.get("subject_type"):
            asset.subject_type = str(v["subject_type"])[:20]
        if v.get("role") in ("evidence", "context", "illustration"):
            asset.asset_role = v["role"]
        if v.get("matches_claim") == "stand_in":
            asset.asset_role = "illustration"
        known_ents = {e["key"] for e in entities}
        ents = [e for e in v.get("entities") or [] if e in known_ents]
        if ents:
            claimed = json.loads(asset.entities_json or "[]")
            asset.entities_json = json.dumps(sorted(set(ents) | set(claimed)), ensure_ascii=False)
        known_facts = {f["id"] for f in facts}
        asset.reveals_json = json.dumps([r for r in v.get("reveals") or [] if r in known_facts])
        try:
            asset.quality_score = round(max(0.0, min(1.0, float(v.get("quality")))), 3)
        except (TypeError, ValueError):
            pass
        if v.get("text_or_logo_in_any_frame"):
            asset.rights_status, asset.rights_reason = R.classify(
                asset.provider, asset.license, asset.source_url, asset.page_url,
                watermark=True)
        asset.verification_json = json.dumps(
            {**v, "reason": reason, "frames": n_frames, "auditor": "video",
             "checks": history, "model": getattr(res, "model", None),
             "provisional_tier": provisional, "tier": tier, "tier_reason": tier_reason},
            ensure_ascii=False)
        db.commit()
        return asset

    async def _check_once(self, db: Session, case: Case, asset: VisualAsset,
                          entities: list[dict], facts: list[dict]):
        frames = await asyncio.to_thread(self._frames, asset)
        times = _piece_times(asset, len(frames))
        context = await asyncio.to_thread(self._context, asset)
        images = ([context[0]] if context[0] else []) + list(frames) + (
            [context[1]] if context[1] else [])
        labels = ((["context before"] if context[0] else [])
                  + [f"{t:.1f}s" for t in times]
                  + (["context after"] if context[1] else []))
        lo, hi = _limits(asset)
        payload = {
            "case": case.canonical_title,
            "frames": labels,
            "piece": {"start": asset.clip_start, "end": asset.clip_end,
                      "may_move_to": [lo, hi]},
            "proposed": {"name": asset.title, "description": asset.description},
            "claimed": {"caption": asset.caption, "found_for": asset.found_for,
                        "source": asset.source_name},
            "entity_list": [{"key": e["key"], "name": e["name"], "type": e["type"],
                             "period": e.get("period")} for e in entities],
            "facts": [{"id": f["id"], "claim": f["claim"]} for f in facts],
        }
        with track_run(db, case.id, "Video Auditor (piece)",
                       input_summary=asset.asset_code) as run:
            v, res = await run_agent("visuals.describe_piece", self.gen, json.dumps(payload, ensure_ascii=False), system=DESCRIBE_SYSTEM, images=_urls(images))
            stamp_run(run, res, "video_auditor")
        return (v if isinstance(v, dict) else {}), res, len(frames)

    def _context(self, asset: VisualAsset) -> tuple[bytes | None, bytes | None]:
        """One frame just before and just after the piece (where the cut is)."""
        if not piece_parent(asset):
            return None, None
        try:
            from app.documentary import storage
            from app.documentary.visuals.video_segmenter import grab_frames

            src = storage.resolve(asset.local_path)
            a, b = float(asset.clip_start or 0), float(asset.clip_end or 0)
            total = _source_length(asset)
            times = [max(a - 0.8, 0.0)] if a > 0.3 else []
            after = [min(b + 0.8, total - 0.05)] if total and b < total - 0.3 else []
            got = grab_frames(src, times + after, 256)
        except Exception:  # noqa: BLE001 — context frames are a help, not a must
            return None, None
        before = got[0] if times else None
        nxt = got[-1] if after else None
        return before or None, nxt or None

    def _move_cut(self, asset: VisualAsset, v: dict) -> bool:
        """Apply the auditor's better start/end (within limits)."""
        from app.core.ai_config import ai_config

        if not piece_parent(asset):
            return False
        try:
            a = float(v.get("suggested_start")) if v.get("suggested_start") is not None \
                else float(asset.clip_start)
            b = float(v.get("suggested_end")) if v.get("suggested_end") is not None \
                else float(asset.clip_end)
        except (TypeError, ValueError):
            return False
        lo, hi = _limits(asset)
        a, b = max(lo, a), min(hi, b)
        cfg = ai_config.footage
        if not (cfg.piece_min_seconds - 0.5 <= b - a <= cfg.piece_max_seconds + 2.0):
            return False
        if abs(a - float(asset.clip_start)) < 0.05 and abs(b - float(asset.clip_end)) < 0.05:
            return False
        asset.clip_start, asset.clip_end = round(a, 3), round(b, 3)
        asset.duration_seconds = round(b - a, 3)
        spec = json.loads(asset.spec_json or "{}")
        spec["window"] = [asset.clip_start, asset.clip_end]
        spec["moved_by_auditor"] = True
        asset.spec_json = json.dumps(spec, ensure_ascii=False)
        try:  # the sheet follows the new window
            from app.documentary import storage
            from app.documentary.visuals.footage import frame_sheet, sheet_path

            frame_sheet(storage.resolve(asset.local_path), a, b,
                        sheet_path(asset.case_id, asset.asset_code))
        except Exception as e:  # noqa: BLE001 — the sheet is only a preview
            import logging

            logging.getLogger(__name__).info("sheet for %s not updated: %s",
                                             asset.asset_code, e)
        return True

    async def placement(self, db: Session, case: Case, asset: VisualAsset,
                        sentences: list[str], story: dict | None = None
                        ) -> tuple[dict, object]:
        """The raw verdict for one piece under these words at this point
        of the story (the caller decides and stores it — same rules as the
        picture auditor)."""
        frames = await asyncio.to_thread(self._frames, asset)
        payload = {
            "case": case.canonical_title,
            "case_status": getattr(case, "resolution_status", None) or "UNKNOWN",
            "frames": f"{len(frames)} frames in order, first to last",
            "narration_while_on_screen": sentences,
            "story": story or {},
            "what_the_piece_is_claimed_to_be": {
                "title": asset.title, "description": asset.description,
                "found_for": asset.found_for, "role": asset.asset_role},
        }
        with track_run(db, case.id, "Video Auditor (placement)",
                       input_summary=asset.asset_code) as run:
            v, res = await run_agent("visuals.audit_piece", self.gen, json.dumps(payload, ensure_ascii=False), system=PLACEMENT_SYSTEM, images=_urls(frames))
            stamp_run(run, res, "video_auditor")
        v = v if isinstance(v, dict) else {}
        if v.get("every_frame_ok") is False and v.get("verdict") == "approved":
            v = {**v, "verdict": "rejected",
                 "reasons": list(v.get("reasons") or []) + ["a frame fails the words"]}
        return v, res

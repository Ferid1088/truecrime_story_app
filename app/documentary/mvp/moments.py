"""Moment cutting: footage for the moments of the film that no picture, video or graphic covers.

A case's videos are kept whole (the proxy of a source) and were cut by meaning into pieces. Most pieces are never
shown: a piece is only chosen when the words over a slot happen to name what it shows. This module goes the other
way round. For every bare moment (what the narrator says, how long it lasts) it

  1. asks the picker which parts of the case's own videos could be on screen while that is said (text only),
  2. shows the cutter the frames of the best parts and lets it find the exact window, cut at scene changes,
  3. stores each window as a new piece (an asset of its own, never a copy of the video),
  4. lets an independent checker judge the finished cut against the words, and
  5. records the result as a requirement of the moment with one candidate per clip, so the planner, the review page
     and the verdict rules treat it like any other approved or doubtful picture.

Only clips the checker approves are placed automatically; doubtful ones wait in the owner's review, and a moment
nothing honestly fits stays bare (reported, never filled with a guess). Nothing here uses the clock to cut a video.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from app.documentary.mvp.needs import fold
from app.documentary.visuals import pieces as PC
from app.documentary.visuals.video_segmenter import _snap, grab_frames
from app.providers.generation import get_generation_provider
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

KEY_PREFIX = "MOM"
SAID_CHARS = 240


def moment_key(beat: str, said: str) -> str:
    """The identity of a bare moment: its beat and the words over it (as the plan's gap report keeps them)."""
    text = " ".join(fold((said or "")[:SAID_CHARS]).split())
    return hashlib.sha1(f"{beat}|{text}".encode()).hexdigest()[:12]


def requirement_key(language: str, key: str) -> str:
    return f"{KEY_PREFIX}.{language}.{key}"


@dataclass
class Moment:
    beat: str
    start: float
    end: float
    said: str
    key: str = ""

    def __post_init__(self):
        self.key = moment_key(self.beat, self.said)

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 2)


@dataclass
class Region:
    """A part of one of the case's videos that was cut by meaning: where it lies and what it shows."""

    code: str
    source: VisualAsset
    start: float
    end: float
    name: str
    description: str

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 2)


@dataclass
class Clip:
    region: Region
    start: float
    end: float
    fit: str
    name: str
    description: str
    why: str

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 2)


@dataclass
class Stats:
    moments: int = 0
    skipped_done: int = 0
    nothing_picked: int = 0
    nothing_cut: int = 0
    cut: int = 0
    approved: int = 0
    needs_review: int = 0
    rejected: int = 0
    stopped_for_budget: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _overlaps(a: float, b: float, taken: list[tuple[float, float]], slack: float = 0.3) -> bool:
    return any(a < y - slack and b > x + slack for x, y in taken)


def check_clips(raw: list, moment: Moment, regions: dict[str, Region], taken: dict[str, list[tuple[float, float]]]
                ) -> list[Clip]:
    """The deterministic check of the cutter's answer: a known region, inside it, cut points snapped to a scene
    change, the allowed length, a fit level that is allowed, not footage that is already used, and no more
    footage than the moment needs. Whatever fails is dropped, never repaired by the clock."""
    cfg = ai_config.mvp().moments
    snap = ai_config.footage.snap_seconds
    allowed = {*cfg.accept_fit, *cfg.review_fit}
    need = moment.seconds + cfg.margin_seconds
    out: list[Clip] = []
    total = 0.0
    for item in raw or []:
        if not isinstance(item, dict) or total >= need - 1e-6:
            continue
        region = regions.get(str(item.get("region")))
        fit = str(item.get("fit") or "").lower()
        name, description = str(item.get("name") or "").strip(), str(item.get("description") or "").strip()
        if region is None or fit not in allowed or not name or not description:
            continue
        try:
            a, b = float(item.get("start")), float(item.get("end"))
        except (TypeError, ValueError):
            continue
        cuts = [float(c) for c in json.loads(region.source.spec_json or "{}").get("scene_changes") or []]
        targets = [region.start, *[c for c in cuts if region.start < c < region.end], region.end]
        a = round(_snap(max(region.start, min(a, region.end)), targets, snap), 3)
        b = round(_snap(max(region.start, min(b, region.end)), targets, snap), 3)
        if b - (a + (need - total)) > 1e-6:                  # no more footage than the moment needs
            b = round(a + max(need - total, cfg.min_clip_seconds), 3)
            b = min(b, region.end)
        length = b - a
        if length < cfg.min_clip_seconds - 0.3 or length > cfg.max_clip_seconds + 0.3:
            continue
        if _overlaps(a, b, taken.get(region.source.asset_code, [])):
            continue
        taken.setdefault(region.source.asset_code, []).append((a, b))
        out.append(Clip(region, a, b, fit, name[:60], description[:600], str(item.get("why") or "")[:300]))
        total += length
    return out


def free_share(a: float, b: float, taken: list[tuple[float, float]]) -> float:
    """The share of [a, b] that no used window covers."""
    covered = 0.0
    for x, y in sorted(taken):
        covered += max(0.0, min(b, y) - max(a, x))
    return max(0.0, 1.0 - covered / max(b - a, 1e-6))


def used_windows(db: Session, case_id: int, used_codes: set[str]) -> dict[str, list[tuple[float, float]]]:
    """The parts of the case's videos that are already on screen in the film (or were cut for a moment)."""
    taken: dict[str, list[tuple[float, float]]] = {}
    rows = db.query(VisualAsset).filter(VisualAsset.case_id == case_id, VisualAsset.asset_type == "video").all()
    for asset in rows:
        info = PC.piece_info(asset)
        spec = json.loads(asset.spec_json or "{}")
        if info and (asset.asset_code in used_codes or spec.get("cut_by") == "moment"):
            w = info["window"]
            taken.setdefault(info["parent"], []).append((float(w[0]), float(w[1])))
    return taken


class MomentCutter:
    def __init__(self, db: Session, case: Case, needs_plan_id: int, language: str, picker=None, cutter=None,
                 checker=None, frames=None):
        self.db, self.case, self.plan_id, self.language = db, case, needs_plan_id, language
        self.cfg = ai_config.mvp().moments
        self._gen = None
        self._picker, self._cutter, self._checker = picker, cutter, checker
        self._frames = frames or grab_frames
        self.stats = Stats()

    # -- the case's own footage ------------------------------------------------
    def trusted_sources(self) -> dict[str, VisualAsset]:
        """Videos of the case that have at least `trusted_min_approved` approved pieces: footage somebody already
        judged to be about this case. A source whose rights block it is never used."""
        approved = {a for (a,) in self.db.query(MvpCandidate.visual_asset_id).join(
            MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id).filter(
            MvpRequirement.case_id == self.case.id, MvpCandidate.final_status == "approved").distinct().all()}
        counts: dict[str, int] = {}
        pieces = self.db.query(VisualAsset).filter(VisualAsset.case_id == self.case.id,
                                                   VisualAsset.asset_type == "video").all()
        for piece in pieces:
            if piece.id in approved and PC.piece_info(piece):
                parent = PC.piece_info(piece)["parent"]
                counts[parent] = counts.get(parent, 0) + 1
        sources = {s.asset_code: s for s in self.db.query(VisualAsset).filter(
            VisualAsset.case_id == self.case.id, VisualAsset.asset_type == PC.SOURCE_TYPE).all()}
        return {code: sources[code] for code, n in counts.items()
                if code in sources and n >= self.cfg.trusted_min_approved
                and VF.rights_decision(sources[code])[0] != "blocked"
                and (p := storage.resolve(sources[code].local_path)) is not None and p.exists()}

    def regions(self, sources: dict[str, VisualAsset]) -> dict[str, Region]:
        out: dict[str, Region] = {}
        for piece in self.db.query(VisualAsset).filter(VisualAsset.case_id == self.case.id,
                                                       VisualAsset.asset_type == "video").all():
            info = PC.piece_info(piece)
            if not info or info["parent"] not in sources:
                continue
            spec = json.loads(piece.spec_json or "{}")
            if spec.get("cut_by") == "moment" or not piece.description:
                continue
            a, b = (float(x) for x in info["window"])
            if b - a >= self.cfg.min_clip_seconds:
                out[piece.asset_code] = Region(piece.asset_code, sources[info["parent"]], a, b,
                                               piece.title or piece.asset_code, piece.description)
        return out

    # -- what was already tried -----------------------------------------------
    def fingerprint(self, regions: dict[str, Region]) -> str:
        return hashlib.sha1(",".join(sorted(regions)).encode()).hexdigest()[:12]

    def done(self, moment: Moment, fingerprint: str) -> bool:
        row = self.db.query(MvpRequirement).filter_by(
            visual_plan_id=self.plan_id, requirement_key=requirement_key(self.language, moment.key)).first()
        if row is None:
            return False
        return json.loads(row.spec_json or "{}").get("moment", {}).get("fingerprint") == fingerprint

    # -- paid calls -------------------------------------------------------------
    def broke(self) -> bool:
        snap = BudgetService(self.db).snapshot(self.case.id)
        if snap.spent_eur >= snap.cap_eur - 1e-9:
            self.stats.stopped_for_budget = True
        return self.stats.stopped_for_budget

    def gen(self):
        self._gen = self._gen or get_generation_provider()
        return self._gen

    async def ask(self, agent: str, label: str, summary: str, payload: dict, images: list[str] | None = None):
        body, gen, pics = json.dumps(payload, ensure_ascii=False), self.gen(), images or None
        calls = {"mvp.moment_pick": lambda: run_agent("mvp.moment_pick", gen, body, images=pics),
                 "mvp.moment_cut": lambda: run_agent("mvp.moment_cut", gen, body, images=pics),
                 "mvp.moment_check": lambda: run_agent("mvp.moment_check", gen, body, images=pics)}
        with track_run(self.db, self.case.id, label, input_summary=summary) as run:
            data, res = await asyncio.wait_for(calls[agent](), ai_config.mvp().call_timeout_s)
            stamp_run(run, res, ai_config.agents[agent].role)
        return data

    async def pick(self, batch: list[Moment], regions: dict[str, Region]) -> dict[str, list[str]]:
        payload = {
            "moments": [{"id": m.key, "beat": m.beat, "seconds": m.seconds, "said": m.said[:SAID_CHARS]}
                        for m in batch],
            "regions": [{"id": r.code, "video": r.source.title, "name": r.name,
                         "description": r.description[:300], "seconds": r.seconds} for r in regions.values()],
            "most_regions_per_moment": self.cfg.regions_per_moment}
        if self._picker is not None:
            data = await self._picker(payload)
        else:
            data = await self.ask("mvp.moment_pick", "Moment cutting: pick", f"{len(batch)} moments", payload)
        out: dict[str, list[str]] = {}
        for item in (data or {}).get("picks") or []:
            if isinstance(item, dict) and item.get("moment") in {m.key for m in batch}:
                out[item["moment"]] = [r for r in item.get("regions") or [] if r in regions][
                    :self.cfg.regions_per_moment]
        return out

    async def cut(self, moment: Moment, picked: list[Region], regions: dict[str, Region],
                  taken: dict[str, list[tuple[float, float]]]) -> list[Clip]:
        shown, images, listing = [], [], []
        for region in picked:
            proxy = storage.resolve(region.source.local_path)
            n = min(self.cfg.frames_per_region, max(3, int(region.seconds)))
            step = region.seconds / n
            times = [round(region.start + step * (i + 0.5), 2) for i in range(n)]
            grabbed = await asyncio.to_thread(self._frames, proxy, times, self.cfg.frame_width)
            kept = [(t, f) for t, f in zip(times, grabbed, strict=False) if f]
            if not kept:
                continue
            cuts = [c for c in json.loads(region.source.spec_json or "{}").get("scene_changes") or []
                    if region.start <= c <= region.end]
            listing.append({"id": region.code, "video": region.source.title, "start": region.start,
                            "end": region.end, "name": region.name, "description": region.description[:300],
                            "frame_times": [t for t, _ in kept], "scene_changes": cuts})
            shown.append(region)
            images += [_data_url(f) for _, f in kept]
        if not shown:
            return []
        payload = {"said": moment.said[:SAID_CHARS], "seconds": moment.seconds,
                   "clip_seconds": {"min": self.cfg.min_clip_seconds, "max": self.cfg.max_clip_seconds,
                                    "wanted": round(moment.seconds + self.cfg.margin_seconds, 1)},
                   "regions": listing}
        if self._cutter is not None:
            data = await self._cutter(payload, images)
        else:
            data = await self.ask("mvp.moment_cut", "Moment cutting: cut", f"{moment.beat} {moment.seconds}s",
                                  payload, images)
        return check_clips((data or {}).get("clips") or [], moment, {r.code: r for r in shown}, taken)

    async def check(self, moment: Moment, asset: VisualAsset) -> dict:
        """The independent verdict on the finished cut: start, middle and end frames of the stored window."""
        frames = await asyncio.to_thread(PC.piece_frames, asset, 1.0, 6, self.cfg.frame_width)
        payload = {"said": moment.said[:SAID_CHARS], "seconds": moment.seconds, "name": asset.title,
                   "description": asset.description,
                   "frame_times": [round(i * ((asset.duration_seconds or 1) / max(len(frames), 1)), 1)
                                   for i in range(len(frames))]}
        images = [_data_url(f) for f in frames]
        if self._checker is not None:
            return await self._checker(payload, images) or {}
        return await self.ask("mvp.moment_check", "Moment cutting: check", asset.asset_code, payload, images) or {}

    # -- the verdict ----------------------------------------------------------
    def decide(self, clip: Clip, verdict: dict) -> tuple[str, list[str]]:
        """approved / needs_review / rejected, and why, from the checker's answer and the cutter's own fit level."""
        cfg = self.cfg
        fit = str(verdict.get("fit") or "no").lower()
        reasons: list[str] = []
        if verdict.get("unusable"):
            return "rejected", ["the cut contains a title card, caption, watermark or presenter"]
        if fit == "no" or fit not in {*cfg.accept_fit, *cfg.review_fit}:
            return "rejected", [f"the checker found that it does not fit the words ({verdict.get('why') or fit})"]
        if verdict.get("misleading"):
            return "rejected", ["the footage would make a viewer believe something the words do not say"]
        if verdict.get("clean_start") is False or verdict.get("clean_end") is False:
            reasons.append("the cut starts or ends in the middle of a movement")
        if verdict.get("sensitive"):
            reasons.append("the footage is sensitive and needs the owner's decision")
        if fit in cfg.review_fit or clip.fit in cfg.review_fit:
            reasons.append("only generic footage of the right kind")
        return ("needs_review", reasons) if reasons else ("approved", [])

    def record(self, moment: Moment, clips: list[tuple[Clip, VisualAsset, str, list[str], dict]],
               fingerprint: str, outcome: str) -> None:
        """One requirement for the moment, one candidate per clip with the statuses the verdict rules read."""
        key = requirement_key(self.language, moment.key)
        row = self.db.query(MvpRequirement).filter_by(visual_plan_id=self.plan_id, requirement_key=key).first()
        spec = {"need": moment.said[:200], "acceptable_roles": ["context"],
                "moment": {"key": moment.key, "language": self.language, "beat": moment.beat,
                           "said": moment.said[:SAID_CHARS], "seconds": moment.seconds,
                           "fingerprint": fingerprint, "outcome": outcome}}
        if row is None:
            row = MvpRequirement(case_id=self.case.id, visual_plan_id=self.plan_id, requirement_key=key,
                                 beat_id=moment.beat, function="moment", entity_key=None, role_needed="context",
                                 priority="low")
            self.db.add(row)
        row.spec_json = json.dumps(spec, ensure_ascii=False)
        row.status = "met" if any(c[2] == "approved" for c in clips) else "open"
        row.shortage_json = None if row.status == "met" else json.dumps({"reason": outcome})
        self.db.flush()
        for order, (clip, asset, status, reasons, verdict) in enumerate(clips):
            asset.description = (str(verdict.get("depicts") or clip.description))[:1000]
            asset.verification_status = "verified" if status == "approved" else (
                "rejected" if status == "rejected" else "needs_review")
            cand = self.db.query(MvpCandidate).filter_by(requirement_id=row.id, visual_asset_id=asset.id).first()
            if cand is None:
                cand = MvpCandidate(requirement_id=row.id, visual_asset_id=asset.id)
                self.db.add(cand)
            prov, _ = VF.provenance_decision(asset)
            rights, _ = VF.rights_decision(asset)
            cand.role = "context"
            cand.provenance_status, cand.rights_status = prov, rights
            # nothing here claims who a person is, so identity only carries "not sure it fits" for the owner
            cand.identity_status = "needs_review" if status == "needs_review" else "approved"
            cand.ethics_status = "needs_review" if verdict.get("sensitive") else "approved"
            cand.scene_status = "rejected" if status == "rejected" else "approved"
            cand.reasons_json = json.dumps(reasons, ensure_ascii=False)
            cand.verifiers_json = json.dumps([{"verifier": "mvp.moment_cut", "fit": clip.fit, "why": clip.why},
                                              {"verifier": "mvp.moment_check", "fit": verdict.get("fit"),
                                               "why": verdict.get("why")}], ensure_ascii=False)
            cand.final_status = VF.final_status(cand)
            spec_asset = json.loads(asset.spec_json or "{}")
            spec_asset["moment"] = {**spec["moment"], "order": order, "seconds": clip.seconds}
            asset.spec_json = json.dumps(spec_asset, ensure_ascii=False)
        self.db.commit()

    # -- one moment, then all -----------------------------------------------------
    async def one(self, moment: Moment, ids: list[str], regions: dict[str, Region],
                  taken: dict[str, list[tuple[float, float]]], fingerprint: str, gate: asyncio.Semaphore) -> None:
        async with gate:
            if self.broke():
                return
            try:
                clips = await self.cut(moment, [regions[i] for i in ids], regions, taken)
            except Exception as e:  # noqa: BLE001 - one moment failing never stops the others
                self.stats.errors.append(f"{moment.beat}: {type(e).__name__}: {e}"[:200])
                log.warning("cutting for %s failed: %s", moment.key, e)
                return
            if not clips:
                self.stats.nothing_cut += 1
                self.record(moment, [], fingerprint, "no footage in the case's videos honestly fits these words")
                return
            stored: list[tuple[Clip, VisualAsset, str, list[str], dict]] = []
            for clip in clips:
                piece = PC.store_pieces(self.db, clip.region.source, [{
                    "start": clip.start, "end": clip.end, "name": clip.name, "description": clip.description,
                    "why_here": clip.why, "cut_by": "moment",
                    "spec_extra": {"moment_key": moment.key}}])[0]
                self.stats.cut += 1
                try:
                    verdict = await self.check(moment, piece)
                except Exception as e:  # noqa: BLE001
                    self.stats.errors.append(f"check {piece.asset_code}: {type(e).__name__}: {e}"[:200])
                    verdict = {"fit": "loose", "why": "the check could not run; the owner decides",
                               "misleading": False}
                status, reasons = self.decide(clip, verdict)
                setattr(self.stats, status, getattr(self.stats, status) + 1)
                stored.append((clip, piece, status, reasons, verdict))
            self.record(moment, stored, fingerprint, "cut")

    async def run(self, gaps: list[dict], used: set[str]) -> dict:
        """Cut footage for the bare moments `gaps` (the plan's report); `used` are the assets already on screen."""
        if not self.cfg.enabled or not gaps:
            return self.stats.as_dict()
        sources = self.trusted_sources()
        regions = self.regions(sources)
        moments = [Moment(g["beat"], g["start"], g["end"], g.get("said") or "") for g in gaps if g.get("said")]
        moments = sorted(moments, key=lambda m: -m.seconds)[:self.cfg.max_moments]
        self.stats.moments = len(moments)
        if not regions or not moments:
            return self.stats.as_dict()
        fingerprint = self.fingerprint(regions)           # the footage the case has, not what is free right now
        todo = [m for m in moments if not self.done(m, fingerprint)]
        self.stats.skipped_done = len(moments) - len(todo)
        taken = used_windows(self.db, self.case.id, used)
        regions = {c: r for c, r in regions.items()
                   if free_share(r.start, r.end, taken.get(r.source.asset_code, [])) * r.seconds
                   >= self.cfg.min_clip_seconds}
        if not regions or not todo:
            return self.stats.as_dict()
        picks: dict[str, list[str]] = {}
        failed: set[str] = set()                  # moments of a pick call that failed are tried again next time
        batches = [todo[i:i + self.cfg.picks_per_call] for i in range(0, len(todo), self.cfg.picks_per_call)]
        pick_gate = asyncio.Semaphore(limit_size("vision"))

        async def pick_one(batch: list[Moment]):
            """(batch, its picks, None) or (batch, None, why not): the batches do not depend on each other."""
            async with pick_gate:
                if self.broke():
                    return batch, None, None
                try:
                    return batch, await self.pick(batch, regions), None
                except Exception as e:  # noqa: BLE001
                    return batch, None, e

        for batch, got, error in await asyncio.gather(*(pick_one(b) for b in batches)):
            if got is not None:
                picks.update(got)
                continue
            failed |= {m.key for m in batch}
            if error is not None:
                self.stats.errors.append(f"pick: {type(error).__name__}: {error}"[:200])
        gate = asyncio.Semaphore(limit_size("vision"))
        jobs = []
        for m in todo:
            if picks.get(m.key):
                jobs.append(self.one(m, picks[m.key], regions, taken, fingerprint, gate))
            elif m.key not in failed:
                self.stats.nothing_picked += 1
                self.record(m, [], fingerprint, "no part of the case's videos was picked for these words")
        await asyncio.gather(*jobs)
        return self.stats.as_dict()


def _data_url(jpeg: bytes) -> str:
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()

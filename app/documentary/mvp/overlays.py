"""Other people's marks in the pictures and footage the film uses. A logo, a watermark, a caption strip, a channel
name or a bar with a logo in it is never shown: the picture or clip is scanned once (a vision model marks each mark
as a box), the film then shows the largest 16:9 part of it that holds none of them, and a picture or clip that cannot
be cut free of them keeps out of the film. The cut is stored on the asset (`spec_json.overlays`) and applied by the
renderer."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from dataclasses import dataclass, field

from PIL import Image
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, MvpCandidate, VisualAsset
from app.documentary import storage
from app.documentary.mvp import verify as VF
from app.documentary.visuals import pieces as PC
from app.providers.generation import get_generation_provider
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

ASPECT = 16 / 9
STEPS = 48                                      # candidate sizes and places of the cut, each way
CLEAN, CROPPED, UNUSABLE = "clean", "cropped", "unusable"


@dataclass(frozen=True)
class Mark:
    kind: str
    what: str
    box: tuple[float, float, float, float]      # shares of the picture's width and height

    def as_json(self) -> dict:
        return {"kind": self.kind, "what": self.what, "box": [round(v, 4) for v in self.box]}


@dataclass
class Scan:
    status: str
    marks: list[Mark] = field(default_factory=list)
    crop: tuple[float, float, float, float] | None = None      # x, y, width, height as shares of the picture

    def as_json(self) -> dict:
        return {"version": ai_config.mvp().overlays.version, "status": self.status,
                "marks": [m.as_json() for m in self.marks],
                "crop": dict(zip("xywh", (round(v, 5) for v in self.crop), strict=True)) if self.crop else None}


def parse_marks(data: dict | None) -> list[Mark]:
    """The marks of the model's answer that are boxes inside the picture, of a known kind."""
    cfg = ai_config.mvp().overlays
    marks = []
    for item in ((data or {}).get("marks") or [])[:cfg.max_marks]:
        box = item.get("box") if isinstance(item, dict) else None
        kind = str(item.get("kind") or "other") if isinstance(item, dict) else "other"
        try:
            x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in box)
        except (TypeError, ValueError):
            continue
        if x1 - x0 < 0.003 or y1 - y0 < 0.003:
            continue
        marks.append(Mark(kind if kind in cfg.kinds else "other", str(item.get("what") or "")[:80],
                          (x0, y0, x1, y1)))
    return marks


def best_crop(marks: list[Mark], width: int, height: int) -> tuple[float, float, float, float] | None:
    """The largest 16:9 window of the picture that touches no mark (each widened by the margin), as shares of the
    picture; None when the window would keep less than `min_keep_area` of the picture. Among equal windows the one
    nearest the picture's centre wins."""
    cfg = ai_config.mvp().overlays
    avoid = [(max(0.0, m.box[0] - cfg.margin) * width, max(0.0, m.box[1] - cfg.margin) * height,
              min(1.0, m.box[2] + cfg.margin) * width, min(1.0, m.box[3] + cfg.margin) * height) for m in marks]
    full = min(float(width), height * ASPECT)
    best: tuple[float, float, float, float, float] | None = None
    for s in range(STEPS + 1):
        w = full * (1.0 - 0.7 * s / STEPS)
        h = w / ASPECT
        if w * h < cfg.min_keep_area * width * height:
            break
        for i in range(STEPS + 1):
            x = (width - w) * i / STEPS
            for j in range(STEPS + 1):
                y = (height - h) * j / STEPS
                if any(x < b[2] and x + w > b[0] and y < b[3] and y + h > b[1] for b in avoid):
                    continue
                off = abs(x + w / 2 - width / 2) / width + abs(y + h / 2 - height / 2) / height
                if best is None or (w, -off) > (best[0], -best[4]):
                    best = (w, x, y, h, off)
        if best is not None:
            break                                   # sizes only get smaller from here
    if best is None:
        return None
    w, x, y, h, _ = best
    return x / width, y / height, w / width, h / height


def judge(marks: list[Mark], width: int, height: int) -> Scan:
    """clean when nothing is laid over the picture, cropped when a window free of every mark keeps enough of it,
    unusable otherwise. A bar (the picture's own border) is left out of the cut when that is possible; when it is
    not (a tall video in a wide frame), the bar stays and only the marks inside the picture are cut away."""
    if not marks:
        return Scan(CLEAN)
    crop = best_crop(marks, width, height) or best_crop([m for m in marks if m.kind != "bar"], width, height)
    if crop:
        return Scan(CROPPED, marks, crop)
    return Scan(UNUSABLE, marks)


def jpeg_of(path, width: int) -> bytes:
    img = Image.open(path).convert("RGB")
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def data_url(jpeg: bytes) -> str:
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def current(asset: VisualAsset) -> Scan | None:
    """The stored scan of this asset when it was made by this version of the scanner for this file."""
    stored = json.loads(asset.spec_json or "{}").get("overlays")
    cfg = ai_config.mvp().overlays
    if not stored or stored.get("version") != cfg.version or stored.get("sha256") != asset.sha256:
        return None
    marks = [Mark(m["kind"], m.get("what", ""), tuple(m["box"])) for m in stored.get("marks") or []]
    crop = stored.get("crop")
    return Scan(stored["status"], marks, tuple(crop[k] for k in "xywh") if crop else None)


def crop_of(asset: VisualAsset) -> dict | None:
    """The cut the renderer applies to this asset (shares of the picture), if it has one."""
    stored = json.loads(asset.spec_json or "{}").get("overlays") or {}
    return stored.get("crop") if stored.get("status") == CROPPED else None


class OverlayScanner:
    def __init__(self, db: Session, case: Case, asker=None, frames=None):
        self.db, self.case = db, case
        self._asker = asker
        self._frames = frames or self.frames_of
        self.cfg = ai_config.mvp().overlays
        self.calls = 0
        self.errors: list[str] = []
        self.stopped_for_budget = False

    def frames_of(self, asset: VisualAsset) -> list[bytes]:
        """JPEG frames of the asset: its sampled frames (a clip) or the picture itself."""
        if asset.asset_type == "video":
            return PC.piece_frames(asset, 1.0, self.cfg.frames_per_video, self.cfg.video_frame_width)
        path = storage.resolve(asset.local_path)
        if path is None or not path.exists():
            raise FileNotFoundError(asset.local_path)
        return [jpeg_of(path, self.cfg.photo_width)]

    async def ask(self, asset: VisualAsset, frames: list[bytes]) -> dict:
        payload = {"kind": "video" if asset.asset_type == "video" else "photo", "max_marks": self.cfg.max_marks,
                   "frames": len(frames)}
        images = [data_url(f) for f in frames]
        if self._asker is not None:
            return await self._asker(payload, images) or {}
        gen = get_generation_provider()
        with track_run(self.db, self.case.id, "Overlay scan", input_summary=asset.asset_code) as run:
            data, res = await asyncio.wait_for(
                run_agent("mvp.overlay_scan", gen, json.dumps(payload), images=images),
                ai_config.mvp().call_timeout_s)
            stamp_run(run, res, ai_config.agents["mvp.overlay_scan"].role)
        self.calls += 1
        return data or {}

    async def scan(self, asset: VisualAsset) -> Scan | None:
        """The stored scan, or a new one (None when the asset could not be read or the model failed: it is
        scanned again next time and not trusted meanwhile)."""
        known = current(asset)
        if known is not None:
            return known
        snap = BudgetService(self.db).snapshot(self.case.id)
        if snap.spent_eur >= snap.cap_eur - 1e-9:
            self.stopped_for_budget = True              # a paid call is never started past the case's cap
            return None
        try:
            frames = await asyncio.to_thread(self._frames, asset)
            data = await self.ask(asset, frames)
        except Exception as e:  # noqa: BLE001 - a failed scan is retried, never taken as "clean"
            self.errors.append(f"{asset.asset_code}: {e}")
            log.warning("overlay scan of %s failed: %s", asset.asset_code, e)
            return None
        if not isinstance(data, dict) or "marks" not in data:
            self.errors.append(f"{asset.asset_code}: the answer has no marks")
            return None
        width, height = Image.open(io.BytesIO(frames[0])).size
        scan = judge(parse_marks(data), width, height)
        spec = json.loads(asset.spec_json or "{}")
        spec["overlays"] = {**scan.as_json(), "sha256": asset.sha256}
        asset.spec_json = json.dumps(spec, ensure_ascii=False)
        self.db.commit()
        return scan

    async def run(self, assets: list[VisualAsset]) -> dict:
        """Scan every asset; reject the candidates of those that cannot be cut free of other people's marks."""
        out = {"scanned": 0, "clean": 0, CROPPED: 0, UNUSABLE: 0, "failed": 0, "rejected_codes": []}
        sem = asyncio.Semaphore(4)

        async def one(asset):
            async with sem:
                return asset, await self.scan(asset)

        for asset, scan in await asyncio.gather(*(one(a) for a in assets)):
            if scan is None:
                out["failed"] += 1
                continue
            out["scanned"] += 1
            out[scan.status] += 1
            if scan.status == UNUSABLE:
                reject(self.db, asset, scan)
                out["rejected_codes"].append(asset.asset_code)
        VF.sync_asset_status(self.db, self.case.id)
        return out


def reject(db: Session, asset: VisualAsset, scan: Scan) -> None:
    """No approved candidate of this asset stays approved: another channel's mark cannot be cut away (candidates
    that were not approved keep their own status)."""
    what = ", ".join(f"{m.kind}: {m.what}" if m.what else m.kind for m in scan.marks)[:200]
    for cand in db.query(MvpCandidate).filter(MvpCandidate.visual_asset_id == asset.id,
                                              MvpCandidate.final_status == "approved"):
        cand.scene_status = "rejected"
        cand.reasons_json = json.dumps(
            json.loads(cand.reasons_json or "[]") + [f"another channel's mark cannot be cut away ({what})"],
            ensure_ascii=False)
        cand.final_status = VF.final_status(cand)
    db.commit()

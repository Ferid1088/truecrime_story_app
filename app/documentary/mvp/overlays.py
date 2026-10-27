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

FRAME = 16 / 9
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


Box = tuple[float, float, float, float]


def _free_rectangles(avoid: list[Box], width: float, height: float):
    """Every rectangle that touches no box and whose edges lie on the picture's edges or on box edges (the only
    places a largest clean rectangle can have them)."""
    xs = sorted({0.0, width, *(v for b in avoid for v in (b[0], b[2]))})
    ys = sorted({0.0, height, *(v for b in avoid for v in (b[1], b[3]))})
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1:]:
            across = [b for b in avoid if b[0] < x1 and b[2] > x0]
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1:]:
                    if not any(b[1] < y1 and b[3] > y0 for b in across):
                        yield x0, y0, x1, y1


def _fit(rect: Box, low: float, high: float, centre: tuple[float, float]) -> Box:
    """The largest window of `rect` whose width/height is between `low` and `high`, as near the centre as it can be."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    if w / h > high:
        w = h * high
    elif w / h < low:
        h = w / low
    x = min(max(centre[0] - w / 2, x0), x1 - w)
    y = min(max(centre[1] - h / 2, y0), y1 - h)
    return x, y, w, h


def _content(marks: list[Mark], width: float, height: float) -> Box:
    """The picture itself: the largest part the bars (its own border, not part of the scene) leave."""
    bars = [(m.box[0] * width, m.box[1] * height, m.box[2] * width, m.box[3] * height) for m in marks if m.kind == "bar"]
    return max(_free_rectangles(bars, width, height), key=lambda r: (r[2] - r[0]) * (r[3] - r[1]),
               default=(0.0, 0.0, width, height))


def best_crop(marks: list[Mark], width: float, height: float) -> tuple[float, float, float, float] | None:
    """The part of the picture to show, as shares of it, or None when none keeps enough of it. It touches no mark (each
    widened by the margin); of the clean windows the largest wins, but a full-screen 16:9 one is taken when it keeps at
    least `frame_preference` of what the largest keeps (a picture of another shape is shown on a blurred fill of itself).
    It keeps at least `min_keep_area` of the picture's own area (its bars left out), and holds the picture's centre."""
    cfg = ai_config.mvp().overlays
    avoid = [(max(0.0, m.box[0] - cfg.margin) * width, max(0.0, m.box[1] - cfg.margin) * height,
              min(1.0, m.box[2] + cfg.margin) * width, min(1.0, m.box[3] + cfg.margin) * height) for m in marks]
    content = _content(marks, width, height)
    centre = ((content[0] + content[2]) / 2, (content[1] + content[3]) / 2)
    floor = cfg.min_keep_area * (content[2] - content[0]) * (content[3] - content[1])

    def usable(win: Box) -> bool:
        if cfg.must_hold_centre and not (win[0] <= centre[0] <= win[0] + win[2] and win[1] <= centre[1] <= win[1] + win[3]):
            return False                    # a mark that can only be avoided off-centre has taken the picture's subject
        return win[2] * win[3] >= floor

    free: list[Box] = []
    frame: list[Box] = []
    best_area = 0.0
    for rect in _free_rectangles(avoid, width, height):
        if (rect[2] - rect[0]) * (rect[3] - rect[1]) < best_area * cfg.frame_preference:
            continue                        # too small to give a window the full-screen rule would still take
        window = _fit(rect, cfg.min_aspect, cfg.max_aspect, centre)
        if usable(window):
            free.append(window)
            best_area = max(best_area, window[2] * window[3])
        full = _fit(rect, FRAME, FRAME, centre)
        if usable(full):
            frame.append(full)

    def best(wins: list[Box]) -> Box | None:
        return max(wins, key=lambda w: (round(w[2] * w[3], 3), -abs(w[0] + w[2] / 2 - centre[0])
                                        - abs(w[1] + w[3] / 2 - centre[1])), default=None)

    largest, full_screen = best(free), best(frame)
    chosen = full_screen if (full_screen and largest and full_screen[2] * full_screen[3]
                             >= cfg.frame_preference * largest[2] * largest[3]) else largest
    if chosen is None:
        return None
    x, y, w, h = chosen
    return x / width, y / height, w / width, h / height


def judge(marks: list[Mark], width: float, height: float) -> Scan:
    """clean when nothing is laid over the picture, cropped when a window free of every mark keeps enough of it,
    unusable otherwise (what is left would be too little of the picture, or not its subject, to mean anything)."""
    if not marks:
        return Scan(CLEAN)
    crop = best_crop(marks, width, height)
    return Scan(CROPPED, marks, crop) if crop else Scan(UNUSABLE, marks)


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
    if asset.width and asset.height:        # the cut follows today's rules, the marks are what the model found
        return judge(marks, asset.width, asset.height)
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
            self.store(asset, known)
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
        self.store(asset, scan)
        return scan

    def store(self, asset: VisualAsset, scan: Scan) -> None:
        """Keep the scan on the asset (the renderer reads the cut from there); nothing is written when it is unchanged."""
        spec = json.loads(asset.spec_json or "{}")
        stored = {**scan.as_json(), "sha256": asset.sha256}
        if spec.get("overlays") == stored:
            return
        spec["overlays"] = stored
        asset.spec_json = json.dumps(spec, ensure_ascii=False)
        self.db.commit()

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

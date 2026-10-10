"""Video segmenter: cuts a video by MEANING, never by the clock.

The segmenter (role video_segmenter, a vision model) watches the whole
video window by window — frames in order with their timestamps, and the
scene changes ffmpeg found — and proposes pieces: each one a complete,
meaningful moment (an action from its start to its end, an establishing
view, one continuous situation), with a name and a description of exactly
what is visible. Pieces may overlap when a meaningful moment needs it.

Its proposals are checked deterministically (inside the video, a sensible
length, cut points snapped to a nearby scene change) and then, piece by
piece, by the independent video auditor: the cut, the name and the
description must be right, or the piece is corrected and checked again —
or dropped. A video the segmenter cannot cut gets no pieces: it is never
cut by the clock instead.
"""

from __future__ import annotations

from app.core.prompts import prompt

import asyncio
import base64
import json
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

SEGMENTER_SYSTEM = prompt("documentary/visuals/video_segmenter/segmenter_system")


def _frame_times(start: float, end: float, fps: float) -> list[float]:
    step = 1.0 / fps
    n = max(1, int((end - start) * fps))
    return [round(start + step * (i + 0.5), 2) for i in range(n)]


def grab_frames(path: Path, times: list[float], width: int) -> list[bytes]:
    out = []
    for t in times:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
             "-vf", f"scale={width}:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg",
             "-"], capture_output=True, timeout=60, check=False)
        out.append(res.stdout if res.returncode == 0 else b"")
    return out


def windows(duration: float, cuts: list[float], size: float) -> list[tuple[float, float]]:
    """Stretches of about `size` seconds, ending at a scene change when
    one is near (a window boundary is never in the middle of a moment if
    it can be helped)."""
    out, t = [], 0.0
    while t < duration - 0.5:
        end = min(t + size, duration)
        if end < duration:
            near = [c for c in cuts if t + size * 0.6 <= c <= t + size]
            if near:
                end = max(near)
        out.append((round(t, 3), round(end, 3)))
        t = end
    return out


def _snap(x: float, targets: list[float], within: float) -> float:
    near = [c for c in targets if abs(c - x) <= within]
    return min(near, key=lambda c: abs(c - x)) if near else x


def check_proposals(raw: list[dict], start: float, end: float, cuts: list[float],
                    duration: float) -> list[dict]:
    """The deterministic check of the segmenter's pieces: inside the
    video, cut points snapped to a scene change within snap_seconds,
    length within the limits (a little slack), no duplicates."""
    cfg = ai_config.footage
    targets = [0.0, *cuts, duration]
    out: list[dict] = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        try:
            a, b = float(p.get("start")), float(p.get("end"))
        except (TypeError, ValueError):
            continue
        name = str(p.get("name") or "").strip()
        desc = str(p.get("description") or "").strip()
        if not name or not desc:
            continue
        a, b = max(0.0, min(a, duration)), max(0.0, min(b, duration))

        a, b = round(_snap(a, targets, cfg.snap_seconds), 3), \
            round(_snap(b, targets, cfg.snap_seconds), 3)
        if b - a < cfg.piece_min_seconds - 0.5 or b - a > cfg.piece_max_seconds + 2.0:
            continue
        if any(abs(q["start"] - a) < 0.3 and abs(q["end"] - b) < 0.3 for q in out):
            continue
        out.append({"start": a, "end": b, "name": name[:60], "description": desc[:600],
                    "why_here": str(p.get("why_here") or "")[:300]})
    return out


class VideoSegmenter:
    def __init__(self, gen=None, frames=None):
        self.gen = gen or get_generation_provider()
        self._grab = frames or grab_frames

    async def cut(self, db: Session, case: Case, source: VisualAsset, proxy: Path,
                  duration: float, cuts: list[float]) -> list[dict]:
        """Pieces [{start, end, name, description, why_here}] of the whole
        video, window by window. Raises when the segmenter fails (the
        caller keeps the video without pieces — never a clock cut)."""
        cfg = ai_config.footage
        system = SEGMENTER_SYSTEM.format(min_s=int(cfg.piece_min_seconds),
                                         max_s=int(cfg.piece_max_seconds))
        found: list[dict] = []
        for a, b in windows(duration, cuts, cfg.segment_window_seconds):
            times = _frame_times(a, b, cfg.segment_fps)
            frames = await asyncio.to_thread(self._grab, proxy, times, cfg.segment_frame_width)
            kept = [(t, f) for t, f in zip(times, frames, strict=False) if f]
            if not kept:
                continue
            payload = {
                "case": case.canonical_title,
                "video": source.title, "caption": source.caption,
                "stretch": [a, b],
                "frame_times": [t for t, _ in kept],
                "scene_changes": [c for c in cuts if a <= c <= b],
            }
            with track_run(db, case.id, "Video Segmenter",
                           input_summary=f"{source.asset_code} {a:.0f}-{b:.0f}s") as run:
                data, res = await self.gen.generate_structured(
                    "video_segmenter", system, json.dumps(payload, ensure_ascii=False),
                    images=["data:image/jpeg;base64," + base64.b64encode(f).decode()
                            for _, f in kept])
                stamp_run(run, res, "video_segmenter")
            raw = (data or {}).get("pieces") if isinstance(data, dict) else None
            found += check_proposals(raw or [], a, b, cuts, duration)
        if len(found) > cfg.max_pieces_per_source:
            # keep the most distinct moments spread over the video
            k = len(found) / cfg.max_pieces_per_source
            found = [found[int(i * k)] for i in range(cfg.max_pieces_per_source)]
        return found

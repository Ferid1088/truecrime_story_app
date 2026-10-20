"""Renders one zoom-in map shot: a MapLibre scene in headless Chromium, frame by frame.

Each frame is set from the camera plan, drawn when the scene is idle (all tiles and labels there) and
captured as PNG; the frames go into ffmpeg at the film's size and frame rate. Nothing depends on the
clock, so the same plan and the same data give the same frames (their hashes are returned)."""

from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.ai_config import ZoomMapConfig, ai_config
from app.core.media_tools import ffmpeg_bin
from app.documentary.zoom_map import camera as CAM
from app.documentary.zoom_map import labels as LAB
from app.documentary.zoom_map import registry as REG
from app.documentary.zoom_map.server import Fetcher, ImageryCache, TileServer


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    path: Path
    seconds: float
    frames: int
    frame_hashes: list[str]
    render_seconds: float
    file_bytes: int
    imagery: list[dict] = field(default_factory=list)
    attribution: list[str] = field(default_factory=list)
    imagery_ends_at: float | None = None
    requests: list[tuple[str, int]] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    tiles_fetched: int = 0
    tiles_cached: int = 0
    last_frame: bytes = b""


def page_config(cfg: ZoomMapConfig, plan: CAM.ZoomPlan, language: str, entries: list) -> dict:
    """What the page needs: style, label expression, imagery layers (zoom limits in MapLibre's
    512 px zoom, which is one below the 256 px tile zoom of the providers)."""
    style = cfg.style
    best = max((e.zoom_max for e in entries), default=None)
    return {
        "lang": language, "flavor": style["flavor"], "background": style["background"],
        "textField": LAB.text_field(language, cfg),
        "fadeLevels": cfg.imagery_fade_levels, "dim": cfg.imagery_dim,
        "imageryEnd": (best - 1) if best is not None else -1.0,
        "buildingOpacity": style["building_opacity"], "buildingColor": style["building_color"],
        "routeColor": style["route_color"], "markerColor": style["marker_color"],
        "extrude": bool(cfg.variants[plan.variant].extrude),
        "imagery": [{
            "id": e.id, "zoomMin": e.zoom_min, "zoomMax": e.zoom_max,
            "mapMin": e.zoom_min - 1, "mapMax": e.zoom_max - 1,
            "bounds": (None if e.coverage == "global" else list(e.coverage[0])),
            "overWater": e.over_water} for e in entries]}


def frame_payload(plan: CAM.ZoomPlan, fr: CAM.Frame, cfg: ZoomMapConfig) -> dict:
    lat, lon = plan.target
    markers = [{"role": "target", "at": [lon, lat]}]
    payload = {"lon": fr.camera.lon, "lat": fr.camera.lat, "zoom": fr.camera.zoom,
               "pitch": fr.camera.pitch, "bearing": fr.camera.bearing, "markers": markers,
               "pulse": fr.pulse, "pulseRadius": float(cfg.marker["radius_px"]), "route": None, "line": 0.0}
    if plan.variant == "route" and plan.other is not None:
        markers.append({"role": "origin", "at": [plan.other[1], plan.other[0]]})
        payload["route"] = [[plan.other[1], plan.other[0]], [lon, lat]]
        payload["line"] = fr.line_progress
    return payload


def encode_command(out: Path, fps: int, crf: int) -> list[str]:
    ff = ffmpeg_bin()
    if not ff:
        raise RenderError("ffmpeg is not available")
    return [ff, "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(out)]


async def render(plan: CAM.ZoomPlan, case_id: int, extract: Path, out: Path, language: str,
                 width: int = 1920, height: int = 1080, fps: int = 25,
                 fetcher: Fetcher | None = None, cfg: ZoomMapConfig | None = None,
                 keep_frames: Path | None = None) -> RenderResult:
    from playwright.async_api import async_playwright

    cfg = cfg or ai_config.zoom()
    fetcher = fetcher or Fetcher()
    lat, lon = plan.target
    entries = REG.entries_for(lat, lon, cfg)
    if plan.other is not None:
        extra = [e for e in REG.entries_for(*plan.other, cfg) if e not in entries]
        entries = sorted(entries + extra, key=lambda e: (-e.resolution_m, e.zoom_min))
    cache = ImageryCache(case_id, fetcher, cfg)
    server = TileServer(case_id, extract, cache, fetcher, cfg)
    cam_frames = CAM.frames(plan, fps, width, height)
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    enc = subprocess.Popen(encode_command(out, fps, int(cfg.render["crf"])), stdin=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    assert enc.stdin is not None and enc.stderr is not None
    hashes: list[str] = []
    png = b""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=list(cfg.render["chromium_args"]))
            page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            await page.route("**/*", server.handle)
            await page.goto("http://maps.local/page")
            await page.evaluate("cfg => start(cfg)", page_config(cfg, plan, language, entries))
            timeout = float(cfg.render["frame_timeout_s"]) * 1000
            for i, fr in enumerate(cam_frames):
                await page.evaluate("f => frame(f)", frame_payload(plan, fr, cfg))
                png = await page.screenshot(type="png", timeout=timeout)
                hashes.append(hashlib.sha256(png).hexdigest())
                if keep_frames is not None:
                    keep_frames.mkdir(parents=True, exist_ok=True)
                    (keep_frames / f"{i:04d}.png").write_bytes(png)
                enc.stdin.write(png)
            error = await page.evaluate("window.mapError")
            await browser.close()
        enc.stdin.close()
        err = enc.stderr.read().decode()
        if enc.wait() != 0:
            raise RenderError(f"ffmpeg failed: {err[-300:]}")
    except Exception:
        enc.kill()
        out.unlink(missing_ok=True)
        raise
    finally:
        cache.save()
    if error:
        out.unlink(missing_ok=True)
        raise RenderError(f"the map scene reported: {error}")
    used = list(cache.used.values())
    return RenderResult(
        path=out, seconds=plan.seconds, frames=len(cam_frames), frame_hashes=hashes,
        render_seconds=round(time.monotonic() - started, 2), file_bytes=out.stat().st_size,
        imagery=[{"id": e.id, "provider": e.provider, "licence": e.licence, "attribution": e.attribution,
                  "zoom_min": e.zoom_min, "zoom_max": e.zoom_max, "resolution_m": e.resolution_m}
                 for e in used],
        attribution=REG.attribution_for(used, cfg),
        imagery_ends_at=max((e.zoom_max for e in entries), default=None),
        requests=server.log, denied=server.denied,
        tiles_fetched=cache.fetched, tiles_cached=cache.from_cache, last_frame=png)

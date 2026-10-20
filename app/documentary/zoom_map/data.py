"""The vector map data of a case: one regional Protomaps/OpenStreetMap extract on disk.

The extract is cut once from a pinned Protomaps build for a box around the case's places and is
rendered offline afterwards, so a map shot costs nothing and looks the same every time. The public
OpenStreetMap tile servers are never used."""

from __future__ import annotations

import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.core.ai_config import ZoomMapConfig, ai_config
from app.documentary import storage
from app.documentary.zoom_map.registry import check_host


class MapDataError(RuntimeError):
    pass


def root() -> Path:
    return storage.ROOT


def case_dir(case_id: int) -> Path:
    d = root() / str(ai_config.zoom().data["work_dir"]) / str(case_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def bbox_around(points: list[tuple[float, float]], margin_km: float) -> tuple[float, float, float, float]:
    """(west, south, east, north) around the places (lat, lon) with a margin in kilometres."""
    if not points:
        raise MapDataError("no place to cut a map extract for")
    lats, lons = [p[0] for p in points], [p[1] for p in points]
    dlat = margin_km / 111.0
    mid = (min(lats) + max(lats)) / 2
    dlon = margin_km / (111.0 * max(math.cos(math.radians(mid)), 0.1))
    return (round(max(min(lons) - dlon, -180.0), 4), round(max(min(lats) - dlat, -85.0), 4),
            round(min(max(lons) + dlon, 180.0), 4), round(min(max(lats) + dlat, 85.0), 4))


def contains(outer: list[float] | tuple, inner: tuple) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def union(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def extract_command(cfg: ZoomMapConfig, source: str, out: Path, bbox: tuple) -> list[str]:
    return [str(root() / str(cfg.data["pmtiles_bin"])), "-q", "extract", source, str(out),
            "--bbox=" + ",".join(f"{v}" for v in bbox), f"--maxzoom={int(cfg.data['max_zoom'])}"]


def ensure_extract(case_id: int, places: list[tuple[float, float]], cfg: ZoomMapConfig | None = None,
                   runner=subprocess.run) -> dict:
    """The case's extract: reused while it covers every place, cut again (box = old box + new places)
    when a place falls outside. Returns {"path", "bbox", "build", "source", "attribution", "created"}."""
    cfg = cfg or ai_config.zoom()
    folder = case_dir(case_id)
    pm, meta_path = folder / "region.pmtiles", folder / "region.json"
    wanted = bbox_around(places, float(cfg.data["bbox_margin_km"]))
    old = json.loads(meta_path.read_text()) if meta_path.exists() and pm.exists() else None
    if old and old.get("build") == cfg.data["build"] and contains(old["bbox"], wanted):
        return {**old, "path": str(pm)}
    if old and old.get("build") == cfg.data["build"]:
        wanted = union(tuple(old["bbox"]), wanted)
    source = str(cfg.data["pmtiles_source"]).format(build=cfg.data["build"])
    check_host(source, cfg)
    tmp = folder / "region.pmtiles.part"
    r = runner(extract_command(cfg, source, tmp, wanted), capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise MapDataError(f"pmtiles extract failed: {(r.stderr or '')[-300:]}")
    tmp.replace(pm)
    meta = {"bbox": list(wanted), "build": cfg.data["build"], "source": source,
            "attribution": cfg.data["vector_attribution"], "licence": "ODbL 1.0 (OpenStreetMap contributors)",
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "bytes": pm.stat().st_size, "max_zoom": int(cfg.data["max_zoom"])}
    meta_path.write_text(json.dumps(meta, indent=2))
    return {**meta, "path": str(pm)}

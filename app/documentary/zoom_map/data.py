"""The vector map data of a case: one regional Protomaps/OpenStreetMap extract on disk.

The extract is cut once from the newest Protomaps build for a box around the case's places and is
rendered offline afterwards, so a map shot costs nothing and looks the same every time. The public
OpenStreetMap tile servers are never used."""

from __future__ import annotations

import json
import math
import subprocess
import urllib.request
from datetime import UTC, datetime, timedelta
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


def build_exists(url: str) -> bool:
    """Whether the provider still serves this build (it keeps only the last days' builds)."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (host checked against the allow-list)
            return response.status == 200
    except OSError:
        return False


def resolve_build(cfg: ZoomMapConfig, exists=build_exists, today: datetime | None = None) -> str:
    """The build to cut from: the configured one, or for "latest" the newest build the provider still serves."""
    wanted = str(cfg.data["build"])
    if wanted != "latest":
        return wanted
    today = today or datetime.now(UTC)
    template = str(cfg.data["pmtiles_source"])
    for back in range(int(cfg.data["build_search_days"]) + 1):
        build = (today - timedelta(days=back)).strftime("%Y%m%d")
        url = template.format(build=build)
        check_host(url, cfg)
        if exists(url):
            return build
    raise MapDataError(f"no Protomaps build of the last {cfg.data['build_search_days']} days is available")


def extract_command(cfg: ZoomMapConfig, source: str, out: Path, bbox: tuple) -> list[str]:
    return [str(root() / str(cfg.data["pmtiles_bin"])), "-q", "extract", source, str(out),
            "--bbox=" + ",".join(f"{v}" for v in bbox), f"--maxzoom={int(cfg.data['max_zoom'])}"]


def ensure_extract(case_id: int, places: list[tuple[float, float]], cfg: ZoomMapConfig | None = None,
                   runner=subprocess.run, exists=build_exists) -> dict:
    """The case's extract: reused while it covers every place, cut again (box = old box + new places)
    when a place falls outside. Returns {"path", "bbox", "build", "source", "attribution", "created"}."""
    cfg = cfg or ai_config.zoom()
    folder = case_dir(case_id)
    pm, meta_path = folder / "region.pmtiles", folder / "region.json"
    wanted = bbox_around(places, float(cfg.data["bbox_margin_km"]))
    old = json.loads(meta_path.read_text()) if meta_path.exists() and pm.exists() else None
    pinned = str(cfg.data["build"]) != "latest"
    same_build = old is not None and (not pinned or old.get("build") == cfg.data["build"])
    if old and same_build and contains(old["bbox"], wanted):
        return {**old, "path": str(pm)}
    if old and same_build:
        wanted = union(tuple(old["bbox"]), wanted)
    build = resolve_build(cfg, exists)
    source = str(cfg.data["pmtiles_source"]).format(build=build)
    check_host(source, cfg)
    tmp = folder / "region.pmtiles.part"
    r = runner(extract_command(cfg, source, tmp, wanted), capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise MapDataError(f"pmtiles extract failed: {(r.stderr or '')[-300:]}")
    tmp.replace(pm)
    meta = {"bbox": list(wanted), "build": build, "source": source,
            "attribution": cfg.data["vector_attribution"], "licence": "ODbL 1.0 (OpenStreetMap contributors)",
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "bytes": pm.stat().st_size, "max_zoom": int(cfg.data["max_zoom"])}
    meta_path.write_text(json.dumps(meta, indent=2))
    return {**meta, "path": str(pm)}

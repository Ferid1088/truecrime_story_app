"""The local runtime of the zoom map: the pmtiles tool and the three JavaScript packages. Both are
fetched once, at the versions pinned in config, and are not part of the repository."""

from __future__ import annotations

import platform
import subprocess
import zipfile
from pathlib import Path

import httpx

from app.core.ai_config import ZoomMapConfig, ai_config
from app.documentary.zoom_map import data as DATA
from app.documentary.zoom_map.registry import check_host


def runtime_dir(cfg: ZoomMapConfig) -> Path:
    return DATA.root() / str(cfg.data["runtime_dir"])


def missing(cfg: ZoomMapConfig | None = None) -> list[str]:
    cfg = cfg or ai_config.zoom()
    rt = runtime_dir(cfg)
    need = {"pmtiles tool": DATA.root() / str(cfg.data["pmtiles_bin"]),
            "maplibre-gl": rt / "node_modules/maplibre-gl/dist/maplibre-gl.js",
            "pmtiles js": rt / "node_modules/pmtiles/dist/pmtiles.js",
            "basemaps js": rt / "node_modules/@protomaps/basemaps/dist/basemaps.js",
            "rtl text js": rt / "node_modules/@mapbox/mapbox-gl-rtl-text/dist/mapbox-gl-rtl-text.js"}
    return [name for name, path in need.items() if not path.exists()]


def ensure_runtime(cfg: ZoomMapConfig | None = None) -> None:
    cfg = cfg or ai_config.zoom()
    lacking = missing(cfg)
    if "pmtiles tool" in lacking:
        _install_pmtiles(cfg)
    if any(n != "pmtiles tool" for n in lacking):
        rt = runtime_dir(cfg)
        rt.mkdir(parents=True, exist_ok=True)
        pkgs = [f"{name}@{version}" for name, version in cfg.data["js_packages"].items()]
        r = subprocess.run(["npm", "install", "--prefix", str(rt), "--silent", *pkgs],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"npm install of the map libraries failed: {r.stderr[-300:]}")


def _install_pmtiles(cfg: ZoomMapConfig) -> None:
    key = f"{platform.system().lower()}_{platform.machine().lower()}"
    plat = cfg.data["pmtiles_platforms"].get(key)
    if plat is None:
        raise RuntimeError(f"no pmtiles release configured for {key}")
    url = str(cfg.data["pmtiles_release"]).format(platform=plat)
    check_host(url, cfg)
    target = DATA.root() / str(cfg.data["pmtiles_bin"])
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / "pmtiles.zip"
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        archive.write_bytes(c.get(url).raise_for_status().content)
    with zipfile.ZipFile(archive) as z:
        z.extract("pmtiles", target.parent)
    target.chmod(0o755)
    archive.unlink()

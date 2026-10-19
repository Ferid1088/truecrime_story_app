"""One export package per enabled platform for a rendered short (YouTube Shorts, Instagram Reels,
Facebook Reels, TikTok). A package is a folder next to the short with the files the platform
upload needs and a package.json that says whether the short fits the platform's length and what
still has to happen before it may be published. Nothing is uploaded: the publishers are stubs."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary import audio as A

SHARED_FILES = ("short.mp4", "cover.png", "subtitles.srt")
DISCLOSURE_PLATFORM = {"youtube_short": "youtube", "tiktok_video": "tiktok",
                       "instagram_reel": "meta", "facebook_reel": "meta"}


def _link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build_platform_packages(short_dir: Path, *, caption: str, cta: str, language: str) -> dict[str, dict]:
    """Write <short_dir>/platforms/<platform>/ for every enabled platform and return their
    package.json contents by platform."""
    manifest = json.loads((short_dir / "manifest.json").read_text(encoding="utf-8"))
    seconds = A.probe_duration(short_dir / manifest["video"])
    packages: dict[str, dict] = {}
    for platform, spec in ai_config.short_form.distribution.items():
        if not spec.enabled:
            continue
        target = short_dir / "platforms" / platform
        target.mkdir(parents=True, exist_ok=True)
        files = {"video": manifest["video"], "cover": manifest["cover"],
                 "subtitles": manifest["subtitles"]}
        for name in files.values():
            _link_or_copy(short_dir / name, target / name)
        fits = spec.min_seconds <= seconds <= spec.max_seconds
        label = manifest["disclosure"]
        package = {
            "schema": "short-form-platform-package-v1",
            "platform": platform, "language": language, "files": files,
            "duration_seconds": round(seconds, 2),
            "length_limits": {"min": spec.min_seconds, "target": spec.target_seconds,
                              "max": spec.max_seconds},
            "length_ok": fits,
            "caption": caption, "cta": cta,
            "disclosure": {"required": label["required"], "label": label["label"],
                           "applies": DISCLOSURE_PLATFORM[platform] in label["platforms"]},
            "captions_burned_in": manifest["captions_burned_in"],
            "rights": manifest["rights"],
            "publisher": {"mode": "stub", "posted": False},
            "before_publishing": (["owner approval of the short"] if fits else
                                  [f"length {seconds:.1f} s is outside {spec.min_seconds}-{spec.max_seconds} s",
                                   "owner approval of the short"]),
        }
        (target / "package.json").write_text(json.dumps(package, indent=2, ensure_ascii=False),
                                             encoding="utf-8")
        packages[platform] = package
    return packages


def package_rendered_short(db, short) -> dict[str, dict]:
    """Packages for a short rendered before platform packages existed: the files are written and
    the short's files_json lists them."""
    from app.documentary import storage

    short_dir = storage.resolve(short.manifest_path).parent
    packages = build_platform_packages(short_dir, caption=short.caption_text or "", cta=short.cta or "",
                                       language=short.language)
    files = json.loads(short.files_json or "{}")
    files["platforms"] = {p: storage.rel(short_dir / "platforms" / p / "package.json") for p in packages}
    short.files_json = json.dumps(files, ensure_ascii=False, sort_keys=True)
    db.commit()
    return packages

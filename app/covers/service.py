"""The covers of one language of a case: the long cover and the cover of each short clip, with the files and a manifest of what each is made of.

The avatar is the host of the film's outfit, the scenes are real pictures of the film, the titles are given (2 to 4 words, in the language), the theme is the
language's. The files are in data/covers/<case>/<language>/ (long.jpg, short_<clip key>.jpg, manifest.json). Which of the platforms' cover ways work is marked
"to_test" in the configuration and in the manifest until a real upload has shown it."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case
from app.documentary import outfits as OF
from app.documentary import storage
from app.covers import avatar as AV
from app.covers import compose as C
from app.covers import scene as SC
from app.thumbnails import hosts as H


def covers_dir(case_id: int, language: str) -> Path:
    d = storage.case_dir(case_id) / "covers" / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def standing_pose(outfit: str) -> H.HostAsset:
    """The first of the configured standing poses the outfit has."""
    have = {a.pose: a for a in H.hosts_for_outfit(outfit)}
    for name in ai_config.covers_settings().avatar["poses"]:
        if name in have:
            return have[name]
    raise AV.AvatarError(f"the outfit {outfit} has none of the standing poses {ai_config.covers_settings().avatar['poses']}")


def film_avatar(db: Session, case_id: int, language: str) -> tuple[str, Path]:
    """The outfit the language's film wears (the one selected for it) and the image of the avatar in it."""
    chosen = OF.selected_outfit(db, case_id, language)
    if chosen is None:
        raise AV.AvatarError(f"no outfit is selected for the {language} film: choose the avatar's outfit first")
    if chosen.outfit_ref:
        return chosen.outfit_ref, standing_pose(chosen.outfit_ref).path
    if chosen.reference_image_path:
        return chosen.name, storage.resolve(chosen.reference_image_path)
    raise AV.AvatarError(f"the outfit {chosen.name} has no image")


def make_covers(db: Session, case: Case, language: str, long_title: str, short_titles: dict[str, str], *, scale: float = 1.0) -> dict:
    """The long cover and one cover for each short clip (`short_titles` maps a clip key to its title; at most `shorts_per_episode`). Raises when a title is
    refused, no scene or no avatar can be found; nothing is written then."""
    cfg = ai_config.covers_settings()
    if len(short_titles) > cfg.shorts_per_episode:
        raise C.CoverError(f"{len(short_titles)} short covers asked, a film has {cfg.shorts_per_episode} shorts")
    for text in (long_title, *short_titles.values()):
        if problems := C.title_issues(text):
            raise C.CoverError(f"{text!r}: {problems[0]}")
    outfit, avatar_image = film_avatar(db, case.id, language)
    logo = storage.ROOT / ai_config.channels[language].logo
    scenes = SC.pick(SC.candidates(db, case, language), 1 + len(short_titles))
    folder = covers_dir(case.id, language)
    portraits = {"long": AV.portrait(avatar_image, cfg.layout["long"]["avatar"]["width"] * cfg.formats["long"].width / (cfg.layout["long"]["avatar"]["height"] * cfg.formats["long"].height),
                                     cfg.layout["long"]["avatar"]["face_widths"]),
                 "short": AV.portrait(avatar_image, cfg.layout["short"]["avatar"]["width"] * cfg.formats["short"].width / (cfg.layout["short"]["avatar"]["height"] * cfg.formats["short"].height),
                                      cfg.layout["short"]["avatar"]["face_widths"])}
    items = [("long", "long", long_title, scenes[0])] + [("short", f"short_{key}", text, scenes[i + 1]) for i, (key, text) in enumerate(sorted(short_titles.items()))]
    made = {}
    for kind, name, text, scene in items:
        made[name] = {**C.compose(kind, language, scene["path"], portraits[kind], logo, text, folder / f"{name}.jpg", scale=scale), "title": text,
                      "scene": {k: scene[k] for k in ("code", "kind", "reused")}, "file": storage.rel(folder / f"{name}.jpg")}
    manifest = {"case_id": case.id, "language": language, "outfit_id": outfit, "avatar_image": storage.rel(avatar_image), "theme": cfg.themes[language].signature, "covers": made,
                "safe_zones": cfg.safe_zones["status"], "platform_cover_ways": {k: v.get("cover_status") for k, v in cfg.platforms.items()}}
    storage.write_atomic(folder / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest

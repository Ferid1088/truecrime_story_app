"""The names of the files that are published. A platform's algorithm and a person who downloads or uploads a file by hand both read its name, so every published file is named for what it is: the
title words of the FULL FILM first (a Short's name always shows which film it belongs to), then the Short's own title words, what kind of file it is (short 3, full documentary), the channel and the
language: `words-of-the-film-title-words-of-the-short-title-short-3-channel-fa.mp4`. The rule is in config/publishing.json (`file_names`), the same for every case and language; Persian and Arabic keep
their own letters, the languages written in the Latin alphabet lose their accents. The named file is a link beside the internal file, which keeps its name, path and hash."""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

from app.core.ai_config import ai_config
from app.publishing.config import FileNameConfig, publishing_config

_FOLDS = str.maketrans({"ß": "ss", "ı": "i", "İ": "i", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ł": "l", "Ł": "l", "đ": "d"})
_NON_WORD = re.compile(r"[^\w]+|_", re.UNICODE)


def words_of(text: str, language: str, cfg: FileNameConfig | None = None, limit: int | None = None) -> list[str]:
    """The words of a text as they go into a file name: normalized, accents folded for a Latin-alphabet language, half-spaces and punctuation as word breaks, lower case, at most `limit` words."""
    cfg = cfg or publishing_config().file_names
    text = unicodedata.normalize("NFC", str(text or "")).replace("‌", " ").replace("‏", " ").replace("‎", " ")
    if language in cfg.ascii_fold_languages:
        text = "".join(ch for ch in unicodedata.normalize("NFKD", text.translate(_FOLDS)) if not unicodedata.combining(ch))
    found = _NON_WORD.sub(" ", text).casefold().split()
    return found[:limit] if limit else found


def file_name(language: str, kind: str, film_title: str, short_title: str = "", number: str | int | None = None, cfg: FileNameConfig | None = None) -> str:
    """The name of a published file (with `.mp4`): `kind` is "film" or "short"; a Short has its own title and number. Too long (in bytes): the Short's words are cut first (down to three), then the film's (down to four),
    then the Short's last words; never the kind, the number, the channel or the language."""
    cfg = cfg or publishing_config().file_names
    film = words_of(film_title, language, cfg, cfg.film_title_words)
    short = words_of(short_title, language, cfg, cfg.short_title_words) if kind == "short" else []
    tail = [*words_of(cfg.kind_words[kind].get(language) or cfg.kind_words[kind]["en"], language, cfg)]
    if kind == "short" and number is not None:
        tail.append(str(number))
    if cfg.include_channel:
        tail += words_of(ai_config.channels[language].name if language in ai_config.channels else "", language, cfg)
    tail.append(language)

    def joined() -> str:
        return cfg.separator.join([*film, *short, *tail])

    def too_long() -> bool:
        return len(joined().encode("utf-8")) + len(".mp4") > cfg.max_bytes

    for words, least in ((short, cfg.min_short_words), (film, cfg.min_film_words), (short, 1)):            # the Short's words first, then the film's, the Short's last words only at the end
        while too_long() and len(words) > least:
            words.pop()
    return f"{joined()}.mp4"


def link(source: Path, name: str, cfg: FileNameConfig | None = None) -> Path:
    """The named copy of a file: a hard link beside it in the configured folder (a copy when the disk cannot link); an older file of that name is replaced, the source is never touched."""
    cfg = cfg or publishing_config().file_names
    target = source.parent / cfg.folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_ino == source.stat().st_ino:
        return target
    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def film_title_of(db, case, language: str) -> str:
    """The title of the full film in a language: its approved editorial title, else the case's name."""
    from app.identity.titles import get_identity

    ident = get_identity(db, case.id, language)
    return (ident.editorial_title if ident is not None and ident.editorial_title else None) or case.canonical_title


def short_title_of(row) -> str:
    """The Short's own title: the one its pack wrote, else its hook."""
    script = json.loads(row.script_json or "{}")
    return (script.get("package") or {}).get("title") or script.get("hook") or ""


def name_all(db, case, language: str, registry=None) -> list[dict]:
    """The named copies of everything the language publishes: its Shorts and its full film (when the registry knows the film's file)."""
    from app.publishing.registry import Kind, PublicationRegistry

    out = name_shorts(db, case, language)
    for asset in (registry or PublicationRegistry(db)).assets(case.id, language):
        if asset.kind is Kind.FILM and asset.path is not None and asset.path.is_file():
            name = file_name(language, "film", film_title_of(db, case, language))
            out.append({"key": asset.key, "name": name, "from": asset.path.name, "linked": str(link(asset.path, name))})
    drop_stale(out)
    return out


def drop_stale(named: list[dict]) -> None:
    """The folder of named copies belongs to the app: a named copy that is not in the current list (its clip was built again, its title changed) is removed. Only the links go, never a source file."""
    folders = {Path(item["linked"]).parent for item in named}
    keep = {Path(item["linked"]) for item in named}
    for folder in folders:
        for old in folder.glob("*.mp4"):
            if old not in keep:
                old.unlink()


def name_shorts(db, case, language: str) -> list[dict]:
    """A named copy of every Short of a case and language, made from the file that is published (its delivery copy when it has one, else the clip). Returns {key, name, from, linked}."""
    from app.documentary import storage
    from app.shortform import clip_media as CM
    from app.shortform import clips as C
    from app.shortform.delivery import delivery_of

    film, out = film_title_of(db, case, language), []
    for row in C.clips_of(db, case.id, language):
        video = (json.loads(row.media_json or "{}").get("video") or {}).get("path")
        original = storage.resolve(video) if video else CM.clip_dir(case.id, language) / f"clip_{row.clip_key}.mp4"
        if not original.is_file():
            continue
        source = delivery_of(original) or original
        name = file_name(language, "short", film, short_title_of(row), "".join(ch for ch in row.clip_key if ch.isdigit()) or row.clip_key)
        out.append({"key": row.clip_key, "name": name, "from": source.name, "linked": str(link(source, name))})
    return out

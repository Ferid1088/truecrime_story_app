"""The opening block stage of one language's chain: the English opening is made once for the film (every language waits for the same one, whichever chain
needs it first), the language's own row is written from it, then its voice, the film's music and the block's audio are made. A language whose opening
does not pass the checks has no block in its film: the stage says so and the film goes on."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy.orm import Session

from app.agents.opening import OpeningWriter
from app.core.ai_config import ai_config
from app.db.models import Case, FilmOpening, StoryVersion
from app.documentary import opening_media as OM
from app.documentary.blueprint import latest_blueprint

_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


def english_version(db: Session, case_id: int) -> StoryVersion | None:
    """The English story the master opening is made from: the newest spoken version, else the newest final."""
    for kind in ("spoken", "final"):
        row = (db.query(StoryVersion).filter(StoryVersion.case_id == case_id, StoryVersion.language == "en", StoryVersion.kind == kind)
               .order_by(StoryVersion.id.desc()).first())
        if row is not None:
            return row
    return None


def _blueprint_of(db: Session, version: StoryVersion):
    return latest_blueprint(db, version.master_version_id) if version.master_version_id else None


def _row_for(db: Session, case_id: int, language: str, kind: str, version_id: int) -> FilmOpening | None:
    return (db.query(FilmOpening).filter(FilmOpening.case_id == case_id, FilmOpening.language == language, FilmOpening.kind == kind,
                                         FilmOpening.story_version_id == version_id, FilmOpening.status == "valid")
            .order_by(FilmOpening.id.desc()).first())


async def master_opening(db: Session, case: Case, writer: OpeningWriter) -> FilmOpening | None:
    """The film's English opening: the valid one of the English story, or a new one (made once, however many chains ask at the same time)."""
    english = english_version(db, case.id)
    if english is None:
        return None
    async with _LOCKS.setdefault((id(asyncio.get_running_loop()), case.id), asyncio.Lock()):      # a lock belongs to the loop it is used in
        existing = _row_for(db, case.id, "en", "master", english.id)
        if existing is not None:
            return existing
        return await writer.create_master(db, case, english, _blueprint_of(db, english))


async def make_opening(db: Session, case: Case, language: str, version: StoryVersion, writer: OpeningWriter | None = None) -> dict:
    """The stage: the language's opening row (written again in the language, or the English master itself) with its voice, music and audio."""
    if not ai_config.opening_block_settings().enabled:
        return {"skipped": True, "reason": "the opening block is switched off"}
    writer = writer or OpeningWriter()
    master = await master_opening(db, case, writer)
    if master is None:
        return {"skipped": True, "reason": "no English story to make the opening from"}
    if master.status != "valid":
        return {"degraded": "the English opening did not pass its checks: " + json.dumps(json.loads(master.validation_json).get("issues"), ensure_ascii=False)[:300],
                "opening_id": master.id}
    if language == "en":
        row = master
    else:
        row = _row_for(db, case.id, language, "native", version.id) or await writer.write_native(db, case, version, master, _blueprint_of(db, version))
        if row.status != "valid":
            return {"degraded": f"the {language} opening did not pass its checks: " + json.dumps(json.loads(row.validation_json).get("issues"), ensure_ascii=False)[:300],
                    "opening_id": row.id}
    media = await OM.make_media(db, case.id, row, master)
    return {"opening_id": row.id, "status": row.status, "seconds": media["audio"]["total"], "hook": json.loads(row.opening_json)["hook"],
            "music_prompt": media["music"]["prompt"]}

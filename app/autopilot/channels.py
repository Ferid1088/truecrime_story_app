"""Channel settings: a placeholder per language and platform that holds a public handle and
never a credential. `connected` stays false until a real integration exists."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import ChannelSetting
from app.utils import utc_now


class UnknownChannel(KeyError):
    pass


def _row_dict(language: str, platform: str, row: ChannelSetting | None) -> dict:
    return {"language": language, "platform": platform,
            "handle": row.handle if row else "", "connected": row.connected if row else False}


def list_channels(db: Session) -> list[dict]:
    cfg = ai_config.autopilot_settings()
    saved = {(r.language, r.platform): r for r in db.query(ChannelSetting).all()}
    return [_row_dict(lang, platform, saved.get((lang, platform)))
            for lang in cfg.languages for platform in cfg.platforms]


def set_handle(db: Session, language: str, platform: str, handle: str) -> dict:
    cfg = ai_config.autopilot_settings()
    if language not in cfg.languages or platform not in cfg.platforms:
        raise UnknownChannel(f"{language}/{platform}")
    row = (db.query(ChannelSetting)
           .filter(ChannelSetting.language == language, ChannelSetting.platform == platform)
           .first())
    if row is None:
        row = ChannelSetting(language=language, platform=platform)
        db.add(row)
    row.handle = handle.strip()
    row.updated_at = utc_now()
    db.commit()
    return _row_dict(language, platform, row)

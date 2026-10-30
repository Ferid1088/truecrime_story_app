"""The photos the owner chose for the pencil drawing, as the planner reads them: in priority order, with the first moment the film
may show each person (when their name has first been spoken) and, for a photo that cannot be drawn at all, the reason."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import VisualAsset
from app.documentary.mvp import describe as DS
from app.documentary.drawing.plan import Portrait
from app.documentary.production.picture_rules import named_by
from app.documentary.visuals.usage import asset_facts

INJURY = re.compile(r"\b(injur\w*|wound\w*|blood\w*|bleeding|corpse|dead body|victim'?s body)\b", re.I)
NEVER = float("inf")


def first_named(slug: str, sentences: list[dict]) -> float:
    """The start of the first sentence that names this person, or never."""
    return next((s["start"] for s in sorted(sentences, key=lambda s: s["start"])
                 if named_by(s.get("display") or s.get("speech") or "", slug)), NEVER)


def people_shown(asset: VisualAsset, person_keys: set[str]) -> list[str]:
    """The people of the case the photo shows: its card's people, else its own person entities."""
    keys = DS.people_of(asset) or list(asset_facts(asset)["entities"])
    return [k for k in dict.fromkeys(keys) if k in person_keys]


def portrait_pool(db: Session, case_id: int, sentences: list[dict], person_keys: set[str]) -> list[Portrait]:
    """The owner's photos with a drawing priority, best first. A photo may start once every person it shows has been named."""
    pool = []
    for asset in (db.query(VisualAsset).filter(VisualAsset.case_id == case_id, VisualAsset.draw_priority.isnot(None))
                  .order_by(VisualAsset.draw_priority).all()):
        earned = max((first_named(e, sentences) for e in people_shown(asset, person_keys)), default=0.0)
        refused = None
        if asset.asset_type != "photo":
            refused = "only a photo can be drawn"
        elif INJURY.search(DS.depicts(asset)):
            refused = "it shows an injury"
        pool.append(Portrait(asset.asset_code, asset.draw_priority, earned, refused))
    return pool


def label(seconds: float) -> str:
    if seconds == NEVER:
        return "never"
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"

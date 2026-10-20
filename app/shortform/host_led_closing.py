"""The closing of one host-led short: the stored (or freshly rendered) host clip that speaks the
outro sentence and the identity line, the timing check with its real length, and the line of the
shared-asset reuse ledger that goes into the publish manifest."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import SharedReelAsset
from app.documentary import audio as A
from app.shortform import closing_library as CL
from app.shortform.closing_render import HeyGenClosingRenderer
from app.shortform.host_led import ReelTiming, build_timing


@dataclass(frozen=True)
class ReelClosing:
    choice: CL.ClosingChoice
    timing: ReelTiming
    seconds: float

    def manifest_entry(self, db: Session) -> dict:
        """What the publish manifest records about the closing: where it came from and the shared
        asset's use ledger (a stored clip is reused, a made-now or fresh one is not)."""
        clip = self.choice.clip
        if clip is None:
            return {"source": "fresh", "seconds": self.seconds, "reused": False}
        ledger = db.query(SharedReelAsset).filter_by(asset_key=clip.asset_key).one()
        return {"source": "stored", "clip_id": clip.id, "asset_key": clip.asset_key,
                "outfit": clip.outfit_label, "position": clip.position, "seconds": self.seconds,
                "reused": not self.choice.rendered, "shared_asset_use_count": ledger.use_count}


async def build_reel_closing(db: Session, language: str, *, case_id: int, hook_seconds: float,
                             film_cut_seconds: float, cover_seconds: float = 0.0,
                             renderer: CL.ClosingRenderer | None = None,
                             rng: random.Random | None = None) -> ReelClosing:
    """The closing under the two settings (``ClosingUnavailable`` says why there is none), and
    the reel's timing with the closing's real length: the avatar cap and the 60 s maximum are
    checked here (``ReelTimingError``)."""
    choice = await CL.resolve_closing(
        db, language, CL.KIND_SHORTS, case_id=case_id, renderer=renderer or HeyGenClosingRenderer(),
        looks=CL.approved_looks(db, language), rng=rng)
    seconds = (choice.clip.duration_seconds if choice.clip is not None
               else A.probe_duration(choice.path))
    timing = build_timing(hook_seconds=hook_seconds, film_cut_seconds=film_cut_seconds,
                          outro_seconds=seconds, cover_seconds=cover_seconds)
    return ReelClosing(choice=choice, timing=timing, seconds=seconds)

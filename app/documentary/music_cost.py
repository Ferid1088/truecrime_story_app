"""Music-generation cost in the ledger (provider_calls), same pattern as
``app/documentary/heygen_cost.py``: ElevenLabs reports no per-call price for sound/music
generation, so every paid generation is booked as an ESTIMATE from the price list
(pricing.json, elevenlabs_sound.eur_per_minute), keyed by the track so booking again never adds
a second entry. Until that price-list entry is confirmed against a real wallet-balance
before/after measurement (the same method already used for heygen.eur_per_minute), it is marked
unverified and booking raises instead of guessing a cost."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import MusicTrack, ProviderCall
from app.services.budget import BudgetService
from app.services.cost_model import load_pricing

STAGE = "elevenlabs:music"
MODEL = "elevenlabs_sound_generation"


class MusicPriceUnverified(RuntimeError):
    """pricing.json has no confirmed elevenlabs_sound rate yet (see module docstring)."""


def book_track(db: Session, track: MusicTrack, case_id: int) -> ProviderCall | None:
    """The estimated cost of one track's generation, once. None while the track has not been
    paid for yet (``characters_paid`` unset)."""
    if not track.characters_paid:
        return None
    key = f"music:{track.track_code}"
    existing = (db.query(ProviderCall)
                .filter(ProviderCall.stage == STAGE, ProviderCall.request_id == key).first())
    if existing is not None:
        return existing
    pricing = load_pricing().get("elevenlabs_sound")
    if not pricing or not pricing.get("verified"):
        raise MusicPriceUnverified(
            "config/parameters/pricing.json has no verified elevenlabs_sound rate; "
            "measure a real wallet balance before/after one generation and set "
            "elevenlabs_sound.verified=true before booking music cost")
    price = float(pricing["eur_per_minute"])
    minutes = track.seconds / 60.0
    return BudgetService(db).record(
        case_id=case_id, stage=STAGE, model=MODEL, units=round(track.seconds, 3),
        cost_eur=round(minutes * price, 6), estimated=True, request_id=key,
        cost_source="estimate")


def book_existing(db: Session, case_id: int) -> list[ProviderCall]:
    """Book every generated track used by this case that has no ledger entry yet."""
    from app.db.models import MusicUsage

    rows = []
    track_ids = {tid for (tid,) in db.query(MusicUsage.track_id)
                 .filter(MusicUsage.case_id == case_id).distinct()}
    for track in db.query(MusicTrack).filter(MusicTrack.id.in_(track_ids)).all():
        try:
            row = book_track(db, track, case_id)
        except MusicPriceUnverified:
            continue
        if row is not None:
            rows.append(row)
    return rows

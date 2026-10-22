"""Music-generation cost in the ledger (provider_calls), same pattern as
``app/documentary/heygen_cost.py``: ElevenLabs reports no per-call price for sound/music
generation, so every paid generation is booked as an ESTIMATE from the price list
(pricing.json, elevenlabs_sound), keyed by the track so booking again never adds a second
entry. The verified rate (``eur_per_minute``) is used when a real wallet-balance before/after
measurement has confirmed it; until then the configured ``assumed_eur_per_minute`` is booked
with ``estimated=true`` and ``cost_source="assumed-unverified"``, so a paid call is never
missing from the ledger while still flagged as unconfirmed everywhere it is shown."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.db.models import MusicTrack, ProviderCall
from app.services.budget import BudgetService
from app.services.cost_model import load_pricing

if TYPE_CHECKING:
    from app.db.models import HostLedMusicTrack

STAGE = "elevenlabs:music"
STAGE_HOST_LED = "elevenlabs:music_host_led"
MODEL = "elevenlabs_sound_generation"


class MusicPriceUnverified(RuntimeError):
    """pricing.json has no confirmed elevenlabs_sound rate yet (see module docstring)."""


def _rate() -> tuple[float, str]:
    """EUR per generated minute and its cost_source tag. A verified price books as
    ``estimate``; the assumed price (unverified, see pricing.json) still books the row —
    flagged — so no paid generation is ever missing from the ledger."""
    pricing = load_pricing().get("elevenlabs_sound") or {}
    if pricing.get("verified") and pricing.get("eur_per_minute"):
        return float(pricing["eur_per_minute"]), "estimate"
    assumed = pricing.get("assumed_eur_per_minute")
    if assumed:
        return float(assumed), "assumed-unverified"
    raise MusicPriceUnverified(
        "config/parameters/pricing.json has no elevenlabs_sound rate at all; set "
        "assumed_eur_per_minute or verify a real wallet before/after measurement")


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
    price, source = _rate()
    minutes = track.seconds / 60.0
    return BudgetService(db).record(
        case_id=case_id, stage=STAGE, model=MODEL, units=round(track.seconds, 3),
        cost_eur=round(minutes * price, 6), estimated=True, request_id=key,
        cost_source=source)


def book_host_led(db: Session, track: HostLedMusicTrack, case_id: int) -> ProviderCall | None:
    """The estimated generation cost of a host-led music track, once per track (keyed by the
    provider's generation id). Booked against the first case that uses it — the track is made
    once for the channel library and the run that triggered the library build paid for it."""
    if not track.provider_generation_id:
        return None
    key = f"music_host_led:{track.provider_generation_id}"
    existing = (db.query(ProviderCall)
                .filter(ProviderCall.stage == STAGE_HOST_LED,
                        ProviderCall.request_id == key).first())
    if existing is not None:
        return existing
    price, source = _rate()
    minutes = (track.duration_seconds or 0) / 60.0
    return BudgetService(db).record(
        case_id=case_id, stage=STAGE_HOST_LED, model=track.model_id or MODEL,
        units=round(track.duration_seconds or 0, 3),
        cost_eur=round(minutes * price, 6), estimated=True, request_id=key,
        cost_source=source)


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

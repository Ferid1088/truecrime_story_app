"""Cross-language production gates for completed, owner-approved films.

These checks reuse the existing hash-bound ApprovalBundle/ApprovalBundleItem
models. They intentionally do not create a second film-approval authority.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ApprovalBundle, ApprovalBundleItem, Video
from app.documentary import storage
from app.services.approval_bundles import film_hash

FILM_LANGUAGES = frozenset({"en", "de", "fa", "ar"})
FINISHED_STATES = ("rendered", "published")


def full_films_by_language(db: Session, case_id: int) -> dict[str, Video]:
    """Latest finished full-length film for each required language in a case."""
    rows = (
        db.query(Video)
        .filter(
            Video.case_id == case_id,
            Video.mode == "full",
            Video.state.in_(FINISHED_STATES),
        )
        .order_by(Video.id.desc())
        .all()
    )
    films: dict[str, Video] = {}
    for row in rows:
        if row.language in FILM_LANGUAGES and row.language not in films:
            films[row.language] = row
    return films


def all_four_full_films_finished(db: Session, case_id: int) -> bool:
    """Pilot films and incomplete language sets do not unlock Shorts."""
    return FILM_LANGUAGES <= full_films_by_language(db, case_id).keys()


def approved_english_film(db: Session, case_id: int) -> Video | None:
    """Return the *latest* completed EN film only if its current file is approved.

    A missing file, fallback metadata hash, excluded bundle item, earlier
    approval for another film, or stale checksum never unlocks production.
    """
    film = full_films_by_language(db, case_id).get("en")
    if film is None or not film.file_path:
        return None
    path = storage.resolve(film.file_path)
    if path is None or not path.is_file():
        return None
    bundle = (
        db.query(ApprovalBundle)
        .filter(ApprovalBundle.case_id == case_id, ApprovalBundle.language == "en")
        .one_or_none()
    )
    if bundle is None:
        return None
    item = (
        db.query(ApprovalBundleItem)
        .filter(
            ApprovalBundleItem.bundle_id == bundle.id,
            ApprovalBundleItem.item_type == "film",
            ApprovalBundleItem.item_id == film.id,
            ApprovalBundleItem.status == "approved",
        )
        .one_or_none()
    )
    if item is None or not item.approved_hash:
        return None
    actual_hash = film_hash(film)
    if item.approved_hash != actual_hash or item.content_hash != actual_hash:
        return None
    return film


def require_english_film_approval(db: Session, case_id: int, language: str, mode: str) -> None:
    """Block non-English full-film production until exact EN output approval."""
    if mode != "full" or language == "en":
        return
    if language not in FILM_LANGUAGES:
        raise ValueError(f"Unsupported documentary language: {language}")
    if approved_english_film(db, case_id) is None:
        raise ValueError(
            "Full-film audiovisual production for DE/FA/AR requires the owner to "
            "watch and explicitly approve the latest complete English film."
        )

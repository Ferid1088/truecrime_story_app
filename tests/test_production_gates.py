"""English-first and four-full-films gates using existing approval tables."""
import hashlib
import uuid

import pytest

from app.db.models import ApprovalBundle, ApprovalBundleItem, Case, Video
from app.services.production_gates import (
    all_four_full_films_finished,
    approved_english_film,
    require_english_film_approval,
)


def _case(db):
    uid = uuid.uuid4().hex[:10]
    case = Case(canonical_title=f"Approval {uid}", slug=f"approval-{uid}", language="en")
    db.add(case)
    db.commit()
    return case


def _film(db, case, language, *, mode="full", path="film.mp4"):
    film = Video(
        case_id=case.id, language=language, mode=mode,
        file_path=path, state="rendered", render_profile="preview",
    )
    db.add(film)
    db.commit()
    return film


def test_english_gate_blocks_de_fa_ar_without_approved_film(db_session):
    case = _case(db_session)
    for language in ("de", "fa", "ar"):
        with pytest.raises(ValueError, match="approve"):
            require_english_film_approval(db_session, case.id, language, "full")
    require_english_film_approval(db_session, case.id, "en", "full")
    require_english_film_approval(db_session, case.id, "de", "pilot")


def test_film_approval_requires_real_file_and_current_hash(db_session, monkeypatch, tmp_path):
    case = _case(db_session)
    film_file = tmp_path / "film.mp4"
    film_file.write_bytes(b"original finished english film")
    from app.services import production_gates
    monkeypatch.setattr(production_gates.storage, "resolve", lambda _: film_file)

    film = _film(db_session, case, "en", path="film.mp4")
    assert approved_english_film(db_session, case.id) is None
    digest = hashlib.sha256(film_file.read_bytes()).hexdigest()
    bundle = ApprovalBundle(case_id=case.id, language="en")
    db_session.add(bundle)
    db_session.flush()
    db_session.add(ApprovalBundleItem(
        bundle_id=bundle.id, item_type="film", item_id=film.id,
        label="English film", content_hash=digest, approved_hash=digest, status="approved",
    ))
    db_session.commit()

    assert approved_english_film(db_session, case.id).id == film.id
    require_english_film_approval(db_session, case.id, "de", "full")

    film_file.write_bytes(b"changed film after approval")
    assert approved_english_film(db_session, case.id) is None
    with pytest.raises(ValueError, match="approve"):
        require_english_film_approval(db_session, case.id, "fa", "full")


def test_shorts_gate_requires_four_full_films(db_session):
    case = _case(db_session)
    _film(db_session, case, "en")
    _film(db_session, case, "de")
    _film(db_session, case, "fa")
    _film(db_session, case, "ar", mode="pilot")
    assert not all_four_full_films_finished(db_session, case.id)
    _film(db_session, case, "ar")
    assert all_four_full_films_finished(db_session, case.id)

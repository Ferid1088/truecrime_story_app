"""Every language other than English shows the English film's map file; a map already made for a language stays in use."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.zoom_map import shot as ZS

INFO = {"place": "Tipp City", "lat": 39.96, "lon": -84.19, "granularity": "city", "return": 0}


def _map(db, case, language, tmp_path, monkeypatch, code):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    path = tmp_path / f"{code}.mp4"
    path.write_bytes(b"video")
    key = ZS.spec_key(language, "Tipp City", 0, ZS.variant_for(INFO), 39.96, -84.19)
    asset = VisualAsset(case_id=case.id, asset_code=code, asset_type="map", provider="generated", local_path=f"{code}.mp4",
                        spec_json=json.dumps({"key": key, "language": language}))
    db.add(asset)
    db.commit()
    return asset


def _case(db):
    case = Case(canonical_title="Maps", slug=f"zm-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


async def never(*args, **kwargs):
    raise AssertionError("no new map is rendered")


def test_another_language_uses_the_english_map_file_and_renders_nothing(db_session, tmp_path, monkeypatch):
    case = _case(db_session)
    english = _map(db_session, case, "en", tmp_path, monkeypatch, "VIS_EN")
    for language in ("de", "ar", "fa"):
        assert asyncio.run(ZS.zoom_asset(db_session, case, language, INFO, renderer=never)).asset_code == english.asset_code


def test_a_map_already_made_for_a_language_is_the_one_that_language_uses(db_session, tmp_path, monkeypatch):
    case = _case(db_session)
    _map(db_session, case, "en", tmp_path, monkeypatch, "VIS_EN")
    persian = _map(db_session, case, "fa", tmp_path, monkeypatch, "VIS_FA")
    assert asyncio.run(ZS.zoom_asset(db_session, case, "fa", INFO, renderer=never)).asset_code == persian.asset_code
    assert asyncio.run(ZS.zoom_asset(db_session, case, "de", INFO, renderer=never)).asset_code == "VIS_EN"


def test_without_an_english_map_another_language_gets_no_new_one(db_session, tmp_path, monkeypatch):
    case = _case(db_session)
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    with pytest.raises(ZS.ZoomMapUnavailable, match="English film's map"):
        asyncio.run(ZS.zoom_asset(db_session, case, "de", INFO, renderer=never))

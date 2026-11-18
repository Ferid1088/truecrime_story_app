"""Stored files are served so that a browser never replays an old copy of a file that was replaced under the same address."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core import files


def test_a_served_file_is_revalidated_each_time_and_a_caller_can_still_set_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "allowed_roots", lambda: [tmp_path.resolve()])
    film = tmp_path / "film.mp4"
    film.write_bytes(b"film")
    response = files.serve(film, "video/mp4")
    assert response.headers["cache-control"] == "no-cache"
    assert files.serve(film, "video/mp4", headers={"Cache-Control": "max-age=60"}).headers["cache-control"] == "max-age=60"
    with pytest.raises(HTTPException):
        files.serve(tmp_path / "missing.mp4", "video/mp4")


def test_a_file_replaced_under_the_same_name_gets_a_new_address(tmp_path):
    film = tmp_path / "film.mp4"
    film.write_bytes(b"old film")
    first = files.versioned_url("/api/film.mp4", film)
    film.write_bytes(b"a longer new film")
    assert first.startswith("/api/film.mp4?v=") and files.versioned_url("/api/film.mp4", film) != first
    assert files.versioned_url("/api/film.mp4", tmp_path / "missing.mp4") == "/api/film.mp4" and files.versioned_url("/api/film.mp4", None) == "/api/film.mp4"

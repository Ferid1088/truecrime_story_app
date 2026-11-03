"""Narration previews: listed, described and streamed from their own folder, never from the film's narration."""

from __future__ import annotations

import pytest

from app.documentary import narration_preview as NP


@pytest.fixture
def previews(tmp_path, monkeypatch):
    monkeypatch.setattr(NP, "BASE", tmp_path)
    monkeypatch.setattr("app.core.files.allowed_roots", lambda: [tmp_path.resolve()])
    (tmp_path / "p1" / "audio").mkdir(parents=True)
    (tmp_path / "p1" / "audio" / "narration.mp3").write_bytes(b"ID3fake")
    NP.write_info("p1", {"title": "T", "case_id": 15, "language": "fa", "created_at": "2026-11-04T10:00:00", "duration_seconds": 12.0, "audio": "audio/narration.mp3",
                         "cost": {"characters_sent": 10, "actual_eur": 0.01}, "blocks": [{"block_id": "b", "start": 0.0, "end": 12.0, "text": "x"}]})
    return tmp_path


def test_the_previews_are_listed_described_and_streamed(client, previews):
    got = client.get("/api/documentary/previews").json()
    assert [p["name"] for p in got] == ["p1"] and got[0]["duration_seconds"] == 12.0
    assert client.get("/api/documentary/previews/p1").json()["blocks"][0]["block_id"] == "b"
    audio = client.get("/api/documentary/previews/p1/audio")
    assert audio.status_code == 200 and audio.content == b"ID3fake" and audio.headers["content-type"] == "audio/mpeg"


def test_an_unknown_preview_and_a_path_in_the_name_are_not_found(client, previews):
    assert client.get("/api/documentary/previews/nope").status_code == 404
    assert client.get("/api/documentary/previews/nope/audio").status_code == 404
    assert client.get("/api/documentary/previews/..%2Fp1/audio").status_code == 404


def test_a_preview_name_cannot_leave_the_previews_folder():
    for bad in ("", "../x", "a/b", ".hidden"):
        with pytest.raises(ValueError):
            NP.folder(bad)


def test_no_previews_is_an_empty_list(client, tmp_path, monkeypatch):
    monkeypatch.setattr(NP, "BASE", tmp_path / "none")
    assert client.get("/api/documentary/previews").json() == []

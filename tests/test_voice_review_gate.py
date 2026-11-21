"""The Persian film waits for the owner's approval of the host's voice and of the opening before the host's videos are made."""

from types import SimpleNamespace

import pytest

from app.documentary import voice_review as VR
from app.documentary.block_review import host_review as HR
from app.documentary.block_review import opening_review as OR
from app.documentary.pipeline.errors import AwaitingVoiceReview

VERSION = SimpleNamespace(id=1, language="fa")


def reviews(monkeypatch, host, opening, host_parts=(1,), opening_parts=(1,)):
    monkeypatch.setattr(HR, "scenes_of", lambda db, v: list(host_parts))
    monkeypatch.setattr(OR, "parts_of", lambda db, v: list(opening_parts))
    monkeypatch.setattr(HR, "current_review", lambda db, v: host)
    monkeypatch.setattr(OR, "current_review", lambda db, v: opening)


def approved():
    return SimpleNamespace(status="approved")


def test_a_host_voice_that_is_not_approved_stops_the_film(monkeypatch):
    reviews(monkeypatch, SimpleNamespace(status="open"), approved())
    with pytest.raises(AwaitingVoiceReview, match="host voice"):
        VR.guard_host_and_opening(None, VERSION, "fa")


def test_an_opening_that_was_never_opened_for_review_stops_the_film(monkeypatch):
    reviews(monkeypatch, approved(), None)
    with pytest.raises(AwaitingVoiceReview, match="opening"):
        VR.guard_host_and_opening(None, VERSION, "fa")


def test_both_approved_lets_the_film_go_on(monkeypatch):
    reviews(monkeypatch, approved(), approved())
    VR.guard_host_and_opening(None, VERSION, "fa")


def test_a_language_the_owner_does_not_hear_first_is_not_held(monkeypatch):
    reviews(monkeypatch, None, None)
    VR.guard_host_and_opening(None, SimpleNamespace(id=1, language="de"), "de")


def test_a_version_without_host_voice_or_opening_has_nothing_to_approve(monkeypatch):
    reviews(monkeypatch, None, None, host_parts=(), opening_parts=())
    VR.guard_host_and_opening(None, VERSION, "fa")

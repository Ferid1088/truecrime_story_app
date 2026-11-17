# ruff: noqa: F811  (the `world` fixture is imported from test_publication_registry and used by name)
"""The approval safeguards of the Publishing Studio, enforced by the backend: a preview-only film cannot be approved; a missing, unapproved or unusable cover blocks approval
(covers are required by default); every requirement is checked again when scheduling and before an upload or a publish, so that an earlier approval is never trusted on its own;
an approval found to be expired is saved at once. Scratch cases and generated media only; no production file is touched. The `world` fixture approves a valid cover for every
asset; tests that need a missing or bad one remove or spoil it."""

from __future__ import annotations

import json
import os
from datetime import timedelta

import pytest
from PIL import Image
from test_publication_registry import events, planned, pub, world  # noqa: F401

from app.core.ai_config import ai_config
from app.db.models import CoverProposal, EpisodeIdentity
from app.publishing.config import publishing_config
from app.publishing.registry import PublishingRefused, ReadinessChanged


def film_pub(w, platform: str):
    return pub(w, platform, "film", f"V{w.film.id}")


def cover_path(w, key: str):
    from app.covers import service as CS

    return CS.covers_dir(w.case.id, "fa") / ("long.jpg" if key == "long" else f"short_{key}.jpg")


def set_cover(w, key: str, *, status: str = "approved", image: str | None = "right") -> None:
    """Add a newer cover proposal for the key (the newest one counts) and write, or not, its final file: `right` shape, `wrong` shape, `broken` (not an image) or None."""
    w.db.add(CoverProposal(case_id=w.case.id, language="fa", cover_key=key, kind="long" if key == "long" else "short", status=status, version=2))
    w.db.commit()
    path = cover_path(w, key)
    if path.exists():
        path.unlink()
    if image == "right":
        Image.new("RGB", (64, 36) if key == "long" else (36, 64), (30, 60, 90)).save(path)
    elif image == "wrong":
        Image.new("RGB", (36, 64) if key == "long" else (64, 36), (30, 60, 90)).save(path)
    elif image == "broken":
        path.write_bytes(b"this is not an image")


def remove_cover(w, key: str) -> None:
    w.db.query(CoverProposal).filter_by(case_id=w.case.id, language="fa", cover_key=key).delete()
    w.db.commit()


def meet_every_film_requirement(w, monkeypatch):
    """A film that satisfies the application's own publish rules: publish render profile, a public case status, an approved episode title, no avatar requirement."""
    w.film.render_profile = "publish"
    w.case.resolution_status = "SOLVED"
    w.db.add(EpisodeIdentity(case_id=w.case.id, channel_id="fa", language="fa", editorial_title="A test film", youtube_title="A test film (Solved)", title_approved=True))
    w.db.commit()
    monkeypatch.setattr(ai_config.host, "required_for_film", False)


def approve_both(w, p):
    w.registry.approve_content(p.id)
    return w.registry.approve_publication(p.id)


def reason_text(response) -> str:
    detail = response.json()["detail"]
    return detail["message"] if isinstance(detail, dict) else detail


# --- 1. a preview-only film ---------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("platform", ["youtube", "facebook"])
def test_a_preview_film_gets_neither_approval(world, platform):
    planned(world)
    film = film_pub(world, platform)
    assert world.film.render_profile == "preview"
    with pytest.raises(PublishingRefused, match="preview profile"):
        world.registry.approve_content(film.id)
    with pytest.raises(PublishingRefused, match="Approve the content first"):
        world.registry.approve_publication(film.id)
    assert film_pub(world, platform).status == "draft" and film_pub(world, platform).content_approved_hash is None


def test_a_forced_content_approval_still_cannot_become_a_publication_approval(world):
    planned(world)
    film = film_pub(world, "youtube")
    film.status, film.content_approved_hash = "content_approved", json.loads(film.hash_inputs_json)["content"]       # as if the content approval had been written around the rules
    world.db.commit()
    with pytest.raises(PublishingRefused, match="preview profile"):
        world.registry.approve_publication(film.id)
    assert film_pub(world, "youtube").publication_approved_hash is None


def test_the_api_refuses_a_preview_film_at_every_step_and_lists_each_reason(world, client):
    planned(world)
    film = film_pub(world, "facebook")
    first = client.post(f"/api/publications/{film.id}/content-approval")
    assert first.status_code == 409 and first.json()["detail"]["reasons"][0] == "rendered with the preview profile — render again with the publish profile"
    assert len(first.json()["detail"]["reasons"]) >= 2 and all(isinstance(r, str) for r in first.json()["detail"]["reasons"])         # each reason on its own, not one run-on sentence
    for step, expected in (("publication-approval", "Approve the content first"), ("schedule", "current, unexpired approval")):
        response = client.post(f"/api/publications/{film.id}/{step}")
        assert response.status_code == 409 and expected in reason_text(response), step
    assert film_pub(world, "facebook").status == "draft"


def test_the_studio_view_tells_the_user_why_the_film_is_blocked_and_what_may_be_done(world, client):
    planned(world)
    view = client.get(f"/api/cases/{world.case.id}/publishing/fa").json()
    film = next(a for a in view["assets"] if a["kind"] == "film")
    for publication in film["publications"]:
        assert any("preview profile" in b for b in publication["blockers"]), publication["platform"]
        assert publication["eligibility"]["approve_content"] is False and publication["eligibility"]["schedule"] is False


# --- 2. covers (required by default) -----------------------------------------------------------------------------------------------------------------------------------

def test_covers_are_required_by_default():
    assert publishing_config().readiness.require_approved_cover is True


def test_a_short_without_an_approved_cover_is_blocked_with_the_shipped_configuration(world):
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    remove_cover(world, "C1")
    with pytest.raises(PublishingRefused, match="cover is not approved"):
        world.registry.approve_content(short.id)


@pytest.mark.parametrize("state, message", [
    ("no_row", "cover is not approved"), ("proposed", "cover is not approved"), ("open", "cover is not approved"), ("skipped", "cover is not approved"),
    ("approved_without_file", "cover is not approved"), ("approved_but_broken", "not a readable image"), ("approved_wrong_shape", "wrong shape"),
])
def test_a_missing_unapproved_or_unusable_cover_blocks_approval(world, state, message):
    planned(world)
    short = pub(world, "tiktok", "short", "C2")
    if state == "no_row":
        remove_cover(world, "C2")
    elif state in ("proposed", "open", "skipped"):
        set_cover(world, "C2", status=state, image=None)
    elif state == "approved_without_file":
        set_cover(world, "C2", image=None)
    elif state == "approved_but_broken":
        set_cover(world, "C2", image="broken")
    else:
        set_cover(world, "C2", image="wrong")
    with pytest.raises(PublishingRefused, match=message):
        world.registry.approve_content(short.id)
    assert pub(world, "tiktok", "short", "C2").status == "draft"


def test_the_api_refuses_a_bad_cover_and_the_view_lists_the_reason_on_its_own_line(world, client):
    planned(world)
    set_cover(world, "C3", image="wrong")
    short = pub(world, "youtube", "short", "C3")
    response = client.post(f"/api/publications/{short.id}/content-approval")
    assert response.status_code == 409 and response.json()["detail"]["reasons"] == ["The approved cover has the wrong shape (9:16 is needed)."]
    view = client.get(f"/api/cases/{world.case.id}/publishing/fa").json()
    c3 = next(p for a in view["assets"] if a["key"] == "C3" for p in a["publications"] if p["platform"] == "youtube")
    assert c3["blockers"] == ["The approved cover has the wrong shape (9:16 is needed)."] and c3["eligibility"]["approve_content"] is False


def test_an_approved_valid_cover_allows_approval_and_joins_the_hash(world):
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    approved = world.registry.approve_content(short.id)
    assert approved.cover_ref and approved.cover_hash and approved.status == "content_approved"
    assert world.registry.approve_publication(short.id).status == "publication_approved"


def test_withdrawing_a_cover_approval_voids_an_approved_publication(world):
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    approve_both(world, short)
    set_cover(world, "C1", status="proposed", image=None)
    world.registry.refresh(pub(world, "youtube", "short", "C1"))
    world.db.commit()
    assert pub(world, "youtube", "short", "C1").status == "voided" and events(world, "approval_voided")


def test_the_film_needs_its_own_long_cover(world, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    film = film_pub(world, "youtube")
    remove_cover(world, "long")
    with pytest.raises(PublishingRefused, match="cover is not approved"):
        world.registry.approve_content(film.id)
    set_cover(world, "long")
    assert world.registry.approve_content(film.id).status == "content_approved"


# --- 3. everything satisfied: approval works (safe fixtures) -----------------------------------------------------------------------------------------------------------

def test_with_every_requirement_met_film_and_shorts_can_be_approved_on_every_destination(world, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    from app.db.models import Publication

    approved = [approve_both(world, p) for p in world.db.query(Publication).filter_by(case_id=world.case.id).all()]
    assert len(approved) == 22 and {p.status for p in approved} == {"publication_approved"}
    assert all(p.cover_hash and p.publication_approved_hash == p.publication_hash and world.registry.approval_valid(p) for p in approved)
    assert all(p.visibility == "private" for p in approved)


def test_with_every_requirement_met_the_release_order_still_holds(world, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    approve_both(world, short)
    with pytest.raises(PublishingRefused, match="film is not published yet"):
        world.registry.schedule(short.id)
    film = film_pub(world, "youtube")
    approve_both(world, film)
    assert world.registry.schedule(film.id).status == "scheduled"                          # the film has no film before it
    world.registry.record_film_published(world.film, url="https://youtu.be/SAFE", published_at=None, episode_number=1)
    short = pub(world, "youtube", "short", "C1")
    assert short.status == "voided"                                                          # its link changed once the film's URL was known
    approve_both(world, short)
    assert world.registry.schedule(short.id).status == "scheduled"


def test_the_buttons_follow_backend_eligibility_for_every_publication(world, client, monkeypatch):
    """`eligibility` in the view is what the Studio's buttons use: it must agree with what the backend does when the action is asked for."""
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    cover_path(world, "C2").write_bytes(b"spoiled")                                          # one Short whose cover stopped being usable
    publications = [p for a in client.get(f"/api/cases/{world.case.id}/publishing/fa").json()["assets"] for p in a["publications"]]
    assert any(not p["eligibility"]["approve_content"] for p in publications) and any(p["eligibility"]["approve_content"] for p in publications)
    for p in publications:
        assert (client.post(f"/api/publications/{p['id']}/content-approval").status_code == 200) is p["eligibility"]["approve_content"], (p["platform"], p["asset_key"])
    publications = [p for a in client.get(f"/api/cases/{world.case.id}/publishing/fa").json()["assets"] for p in a["publications"]]
    for p in publications:
        assert (client.post(f"/api/publications/{p['id']}/publication-approval").status_code == 200) is p["eligibility"]["approve_publication"], (p["platform"], p["asset_key"])
    publications = [p for a in client.get(f"/api/cases/{world.case.id}/publishing/fa").json()["assets"] for p in a["publications"]]
    for p in publications:
        assert (client.post(f"/api/publications/{p['id']}/schedule").status_code == 200) is p["eligibility"]["schedule"], (p["platform"], p["asset_key"], p["status"])
    scheduled = [p for p in publications if p["eligibility"]["schedule"]]
    assert scheduled and all(p["kind"] == "film" for p in scheduled)                         # only the films: the Shorts wait for them


# --- 4. an approval is never trusted on its own: the requirements are checked again -------------------------------------------------------------------------------------

def test_scheduling_checks_the_requirements_again(world, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    film = film_pub(world, "youtube")
    approve_both(world, film)
    world.db.query(EpisodeIdentity).filter_by(case_id=world.case.id).update({"title_approved": False})
    world.db.commit()
    with pytest.raises(ReadinessChanged, match="episode title"):
        world.registry.schedule(film.id)
    again = film_pub(world, "youtube")
    assert again.status == "voided" and "episode title" in again.error and again.publication_approved_hash is None
    assert [e.reason for e in events(world, "readiness_blocked")] == [again.error]
    assert json.loads(events(world, "readiness_blocked")[0].context_json)["stage"] == "schedule"


@pytest.mark.parametrize("stage", ["schedule", "upload", "publish"])
def test_every_stage_blocks_when_the_film_stops_meeting_the_publish_rules(world, monkeypatch, stage):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    film = film_pub(world, "facebook")
    approve_both(world, film)
    world.film.render_profile = "preview"                                                     # not a change of the file: the hashes cannot tell
    world.db.commit()
    with pytest.raises(ReadinessChanged, match="preview profile") as raised:
        world.registry.assert_ready(film_pub(world, "facebook"), stage)
    assert raised.value.reasons == ["rendered with the preview profile — render again with the publish profile"]
    assert film_pub(world, "facebook").status == "voided"
    assert json.loads(events(world, "readiness_blocked")[-1].context_json)["stage"] == stage


def test_an_approved_publication_whose_media_check_now_fails_is_blocked_before_a_live_operation(world):
    planned(world)
    short = pub(world, "instagram", "short", "C1")
    approve_both(world, short)
    short = pub(world, "instagram", "short", "C1")
    data = json.loads(short.hash_inputs_json)
    data["readiness"]["preflight"] = {"for": short.content_hash, "verdict": "FAIL", "failing": ["no edit list"], "error": None}      # the stored media check now says FAIL
    short.hash_inputs_json = json.dumps(data, sort_keys=True)
    world.db.commit()
    with pytest.raises(ReadinessChanged, match="Instagram media check: no edit list"):
        world.registry.assert_ready(pub(world, "instagram", "short", "C1"), "upload")
    assert not world.registry.approval_current(pub(world, "instagram", "short", "C1"))


def test_a_file_swapped_without_changing_its_size_or_time_is_caught_by_the_fresh_hash(world):
    planned(world)
    short = pub(world, "tiktok", "short", "C1")
    approve_both(world, short)
    path = world.clips / "clip_C1.mp4"
    before = path.stat()
    data = bytearray(path.read_bytes())
    data[-100] ^= 0xFF
    path.write_bytes(bytes(data))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert world.registry.refresh(pub(world, "tiktok", "short", "C1")).status == "publication_approved"        # the stat-keyed cache cannot see it
    with pytest.raises(ReadinessChanged, match="differs from the one that was approved"):
        world.registry.assert_ready(pub(world, "tiktok", "short", "C1"), "upload")
    assert pub(world, "tiktok", "short", "C1").status == "voided"


def test_a_withdrawn_cover_stops_scheduling_and_the_reason_is_saved(world, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    film = film_pub(world, "youtube")
    approve_both(world, film)
    set_cover(world, "long", status="proposed", image=None)
    with pytest.raises(ReadinessChanged):
        world.registry.schedule(film.id)
    assert film_pub(world, "youtube").status == "voided" and events(world, "approval_voided")


def test_the_api_enforces_the_recheck_and_answers_with_the_reasons(world, client, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    film = film_pub(world, "youtube")
    approve_both(world, film)
    world.db.query(EpisodeIdentity).filter_by(case_id=world.case.id).update({"title_approved": False})
    world.db.commit()
    response = client.post(f"/api/publications/{film.id}/schedule")
    assert response.status_code == 409 and response.json()["detail"]["reasons"] == ["no approved episode title (Naming tab)"]
    assert film_pub(world, "youtube").status == "voided"
    view = client.get(f"/api/cases/{world.case.id}/publishing/fa").json()
    shown = next(p for a in view["assets"] if a["kind"] == "film" for p in a["publications"] if p["platform"] == "youtube")
    assert shown["status"] == "voided" and shown["blockers"] == ["no approved episode title (Naming tab)"]
    assert shown["error"] is None                                                           # the same reason is not shown twice


# --- 5. an expired approval is saved when an operation finds it --------------------------------------------------------------------------------------------------------

def test_the_age_limit_applies_to_a_fully_ready_publication(world, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    film = film_pub(world, "facebook")
    approve_both(world, film)
    world.clock["now"] += timedelta(days=publishing_config().approval.max_age_days + 1)
    with pytest.raises(PublishingRefused, match="current, unexpired approval|no current approval"):
        world.registry.schedule(film.id)


@pytest.mark.parametrize("operation", ["schedule", "upload", "publish"])
def test_an_operation_that_finds_an_expired_approval_saves_it_at_once(world, monkeypatch, operation):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    film = film_pub(world, "facebook")
    approve_both(world, film)
    world.clock["now"] += timedelta(days=publishing_config().approval.max_age_days + 1)
    with pytest.raises(PublishingRefused):
        if operation == "schedule":
            world.registry.schedule(film.id)
        else:
            world.registry.assert_ready(film_pub(world, "facebook"), operation)
    # no snapshot, no Studio: the state and the audit event are in the database already
    assert film_pub(world, "facebook").status == "expired" and film_pub(world, "facebook").publication_approved_hash is None
    assert len(events(world, "approval_expired")) == 1


def test_the_api_saves_an_expiry_found_by_a_refused_schedule(world, client, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    planned(world)
    film = film_pub(world, "youtube")
    approve_both(world, film)
    film.publication_approved_at = world.clock["now"] - timedelta(days=publishing_config().approval.max_age_days + 1)
    world.db.commit()
    assert client.post(f"/api/publications/{film.id}/schedule").status_code == 409
    assert film_pub(world, "youtube").status == "expired" and len(events(world, "approval_expired")) == 1


def test_an_error_that_is_not_a_blocking_reason_is_still_shown(world):
    planned(world)
    short = pub(world, "tiktok", "short", "C1")
    short.status, short.error = "failed", "The platform refused the upload."
    world.db.commit()
    assert world.registry.view(pub(world, "tiktok", "short", "C1"))["error"] == "The platform refused the upload."
    short = pub(world, "tiktok", "short", "C1")
    set_cover(world, "C1", status="proposed", image=None)
    short.error = "The cover is not approved."
    world.db.commit()
    assert world.registry.view(pub(world, "tiktok", "short", "C1"))["error"] is None            # it only repeats the listed blocker

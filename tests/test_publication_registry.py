"""Phase 2 of the publishing layer: the publication registry (plan, approvals bound to a hash, routing links, the film's URL written in one place), streaming approval hashing,
the Monitoring findings and the API. Real small videos made with ffmpeg; the database is the suite's (SQLite by default; run with TC_TEST_DATABASE_URL on a *_test PostgreSQL database to run it on PostgreSQL, as done for Phase 2); nothing is uploaded or published."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import tracemalloc
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import _delete_case_tree
from PIL import Image
from sqlalchemy import text

from app.core.files import file_sha256
from app.db.models import (
    Case,
    CoverProposal,
    FileHashCache,
    PolicyEvent,
    ProductionScript,
    Publication,
    PublicationAccount,
    RoutingLink,
    ShortClip,
    StoryVersion,
    Video,
)
from app.documentary import storage
from app.lifecycle import videos as V
from app.publishing import hashing
from app.publishing.config import publishing_config
from app.publishing.registry import PublicationRegistry, PublishingRefused
from app.utils import utc_now

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is needed to make the test videos")
ROOT = Path(__file__).resolve().parents[1]
START = date(2030, 1, 7)


def _video(path: Path, *, seconds: int = 5, editlist: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = [] if editlist else ["-use_editlist", "0"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=540x960:rate=30:duration={seconds}", "-f", "lavfi", "-i", f"sine=sample_rate=48000:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "60", "-sc_threshold", "0", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-movflags", "+faststart", *flags, str(path)],
                   check=True)
    return path


@pytest.fixture
def world(db_session, tmp_path, monkeypatch):
    """A case with a Farsi film (a real Video row) and five valid ShortClips, files on disk. Film readiness rules are those of the application, except where a test says so."""
    monkeypatch.setattr("app.core.ai_config.ai_config.documentary.storage_dir", str(tmp_path / "cases"))
    case = Case(canonical_title="Registry test", slug=f"registry-test-{uuid.uuid4().hex[:8]}", language="fa")
    db_session.add(case)
    db_session.commit()
    base = tmp_path / "cases" / str(case.id)
    variants = [_video(base / "work" / f"v{n}.mp4", seconds=4 + n) for n in range(3)]
    for number in range(1, 6):
        key = f"C{number}"
        target = base / "clips" / "fa" / f"clip_{key}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(variants[number % 3], target)
        db_session.add(ShortClip(case_id=case.id, language="fa", clip_key=key, status="valid", media_json=json.dumps({"video": {"path": storage.rel(target), "seconds": 5}})))
    master = StoryVersion(case_id=case.id, version=1, kind="final", narrative_angle="{}", language="en", story_text="x", narrative_structure=json.dumps({"title": "A test film"}))
    db_session.add(master)
    db_session.flush()
    script = ProductionScript(case_id=case.id, story_version_id=master.id, language="fa", mode="full", script_json="{}")
    db_session.add(script)
    db_session.flush()
    film_file = base / "renders" / "fa" / "film.mp4"
    film_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(variants[0], film_file)
    film = V.register_render(db_session, None, script, {"path": storage.rel(film_file), "duration": 4})
    db_session.commit()
    from app.covers import service as covers_service

    for key in ("long", "C1", "C2", "C3", "C4", "C5"):                       # every cover approved, with a real file of the right shape: covers are required by default
        db_session.add(CoverProposal(case_id=case.id, language="fa", cover_key=key, kind="long" if key == "long" else "short", status="approved", version=1))
        Image.new("RGB", (64, 36) if key == "long" else (36, 64), (30, 60, 90)).save(covers_service.covers_dir(case.id, "fa") / ("long.jpg" if key == "long" else f"short_{key}.jpg"))
    db_session.commit()
    sleeps = {"now": utc_now()}
    registry = PublicationRegistry(db_session, now=lambda: sleeps["now"])
    monkeypatch.setattr(registry.cfg.approval, "default_approved", False)         # these tests are about the owner's own approvals; the default approval has its own tests
    monkeypatch.setattr(registry.cfg.plan, "make_delivery_copies", False)         # these tests send the clips as they are; the delivery copies have their own test below
    monkeypatch.setattr(registry.cfg.scheduling, "film_before_shorts", False)     # most tests publish a Short with no film; the film-first rule has its own tests
    yield type("World", (), {"case": case, "film": film, "film_file": film_file, "clips": base / "clips" / "fa", "db": db_session, "registry": registry, "clock": sleeps, "tmp": tmp_path})
    db_session.rollback()
    ids = [p.id for p in db_session.query(Publication).filter_by(case_id=case.id).all()]
    db_session.query(RoutingLink).filter(RoutingLink.publication_id.in_(ids)).delete(synchronize_session=False)
    db_session.query(Publication).filter_by(case_id=case.id).delete()
    db_session.query(PolicyEvent).filter_by(case_id=case.id).delete()
    db_session.commit()
    _delete_case_tree(db_session, case.id)


def planned(w, **kw):
    return w.registry.plan(w.case.id, "fa", start_date=START, **kw)


def pub(w, platform, kind, key) -> Publication:
    w.db.expire_all()
    return w.db.query(Publication).filter_by(case_id=w.case.id, platform=platform, kind=kind, asset_key=key).one()


def ready_film(monkeypatch):
    monkeypatch.setattr(V, "publish_blockers", lambda db, video: [])


def events(w, decision: str) -> list[PolicyEvent]:
    return w.db.query(PolicyEvent).filter_by(case_id=w.case.id, policy="publication", decision=decision).all()


def test_a_plan_approves_by_default_what_is_ready_and_leaves_what_is_not(world, monkeypatch):
    monkeypatch.setattr(world.registry.cfg.approval, "default_approved", True)
    monkeypatch.setattr(world.registry.cfg.approval, "manual_languages", [])
    planned(world)
    ready = pub(world, "facebook", "short", "C1")
    assert ready.status == "draft"                                                    # no platform text yet: not ready, so not approved
    world.registry.update(ready.id, metadata={"title": "A title", "hashtags": [], "keywords": [], "cta": ""})
    planned(world)
    assert pub(world, "facebook", "short", "C1").status == "publication_approved" and pub(world, "facebook", "short", "C2").status == "draft"
    assert {json.loads(e.context_json)["actor"] for e in events(world, "content_approved") + events(world, "publication_approved")} == {"default"}
    planned(world)
    assert len(events(world, "publication_approved")) == 1                              # nothing is approved twice


def test_a_plan_first_approves_the_planners_covers_that_have_their_picture(world, monkeypatch):
    seen = []
    monkeypatch.setattr("app.covers.service.auto_approve", lambda db, case, language: seen.append((case.id, language)) or [])
    planned(world)
    assert seen == [(world.case.id, "fa")]


def texts(world, **by_platform):
    """Give the Short C1 of every portal its own text: {platform: (caption, title, hashtags)}."""
    planned(world)
    for platform, (caption, title, tags) in by_platform.items():
        row = pub(world, platform, "short", "C1")
        world.registry.update(row.id, caption=caption, metadata={"title": title, "hashtags": tags, "keywords": [], "cta": ""})


def test_a_word_corrected_on_one_portal_is_corrected_on_the_others_by_the_words_around_it(world):
    texts(world, youtube=("اشلی در گاراژ بود و تپانچه نداشت", "تپانچه در گاراژ", ["#پرونده"]), facebook=("در گاراژ بود و تپانچه نداشت، پس چرا؟", "تپانچه ای در گاراژ", ["#پرونده"]),
          instagram=("او تپانچه نداشت", "", ["#پرونده"]), tiktok=("متن دیگر بدون آن کلمه", "", []))
    source = pub(world, "youtube", "short", "C1")
    before = world.registry.text_state(source)
    world.registry.update(source.id, caption="اشلی در گاراژ بود و تپانچهٔ نداشت")
    out = world.registry.propagate_correction(source.id, before)
    assert {c["platform"] for c in out["changed"]} == {"facebook", "instagram"} and out["skipped"] == [] and out["unplaced"] == 0
    assert "تپانچهٔ نداشت" in pub(world, "facebook", "short", "C1").caption and "تپانچهٔ نداشت" in pub(world, "instagram", "short", "C1").caption
    assert pub(world, "tiktok", "short", "C1").caption == "متن دیگر بدون آن کلمه"                                  # nothing to correct there
    assert "تپانچه" in pub(world, "youtube", "short", "C1").metadata_json                                          # the source is only what the owner saved
    assert events(world, "correction_propagated")


def test_a_correction_changes_the_other_portals_as_an_owner_edit_and_voids_their_approval(world):
    texts(world, youtube=("متن اولیه درست است", "عنوان", []), instagram=("متن اولیه درست است و ادامه", "", []))
    ig = pub(world, "instagram", "short", "C1")
    world.registry.approve_content(ig.id)
    assert pub(world, "instagram", "short", "C1").status == "content_approved"
    source = pub(world, "youtube", "short", "C1")
    before = world.registry.text_state(source)
    world.registry.update(source.id, caption="متن اولیه غلط است")
    world.registry.propagate_correction(source.id, before)
    ig = pub(world, "instagram", "short", "C1")
    assert ig.caption == "متن اولیه غلط است و ادامه" and ig.status == "voided" and ig.metadata_source == "owner"


def test_a_short_piece_alone_is_never_replaced_and_added_or_deleted_text_is_reported_not_carried(world):
    texts(world, youtube=("به خانه رفت و به مدرسه", "", []), facebook=("او به مدرسه نرفت", "", []))
    source = pub(world, "youtube", "short", "C1")
    before = world.registry.text_state(source)
    world.registry.update(source.id, caption="با خانه رفت و به مدرسه رسید")
    out = world.registry.propagate_correction(source.id, before)
    assert pub(world, "facebook", "short", "C1").caption == "او به مدرسه نرفت"                                      # "به" alone would have hit this sentence
    assert out["unplaced"] == 1 and out["changed"] == []


def test_a_portal_that_is_already_public_is_listed_not_changed(world):
    texts(world, youtube=("متن قدیمی", "", []), instagram=("متن قدیمی", "", []))
    ig = pub(world, "instagram", "short", "C1")
    ig.status = "published"
    world.db.commit()
    source = pub(world, "youtube", "short", "C1")
    before = world.registry.text_state(source)
    world.registry.update(source.id, caption="متن جدید")
    out = world.registry.propagate_correction(source.id, before)
    assert out["changed"] == [] and [(s["platform"], s["reason"]) for s in out["skipped"]] == [("instagram", "it is published")]
    assert pub(world, "instagram", "short", "C1").caption == "متن قدیمی"


def test_the_text_of_an_upload_that_is_not_public_yet_can_still_be_corrected_but_not_its_time(world):
    planned(world)
    row = pub(world, "facebook", "short", "C1")
    world.registry.update(row.id, caption="متن اول")
    row = pub(world, "facebook", "short", "C1")
    row.status, was = "ready", row.scheduled_at
    world.db.commit()
    done = world.registry.update(row.id, caption="متن دوم", scheduled_local="2031-01-01T10:00", visibility="public")
    assert done.caption == "متن دوم" and done.scheduled_at == was and done.visibility == "private"
    assert world.registry.view(done)["eligibility"]["edit"] is True
    done.status = "published"
    world.db.commit()
    with pytest.raises(PublishingRefused):
        world.registry.update(done.id, caption="متن سوم")


def test_the_edit_route_answers_with_what_was_corrected_elsewhere(world, client):
    texts(world, youtube=("اشلی تپانچه نداشت", "", []), tiktok=("او تپانچه نداشت", "", []))
    source = pub(world, "youtube", "short", "C1")
    answer = client.patch(f"/api/publications/{source.id}", json={"caption": "اشلی تپانچهٔ نداشت"})
    assert answer.status_code == 200 and [c["platform"] for c in answer.json()["propagation"]["changed"]] == ["tiktok"]
    assert answer.json()["propagation"]["pieces"] == [{"old": "تپانچه", "new": "تپانچهٔ"}]


def test_the_overview_gives_every_publication_one_lamp_state(world):
    planned(world)
    states = {(r["platform"], r["asset_key"]): r["state"] for r in world.registry.overview(world.case.id)}
    assert set(states.values()) == {"waiting"}                                 # a fresh plan has times and nothing is published yet
    for platform, key, status, scheduled in (("tiktok", "C1", "published", True), ("tiktok", "C2", "failed", True), ("tiktok", "C3", "publish_uncertain", True),
                                              ("tiktok", "C4", "draft", False), ("tiktok", "C5", "voided", True)):
        row = pub(world, platform, "short", key)
        row.status = status
        row.scheduled_at = row.scheduled_at if scheduled else None
        world.db.commit()                                                      # `pub` expires the session: commit before the next lookup
    got = {r["asset_key"]: r["state"] for r in world.registry.overview(world.case.id) if r["platform"] == "tiktok"}
    assert got == {"C1": "published", "C2": "failed", "C3": "uncertain", "C4": "unscheduled", "C5": "unscheduled"}


# --- the plan --------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_plan_covers_the_film_and_five_shorts_on_every_destination_and_is_idempotent(world):
    first = planned(world)
    assert len(first) == 2 + 5 * 4
    assert {(p.platform, p.kind) for p in first if p.kind == "film"} == {("youtube", "film"), ("facebook", "film")}
    assert {p.asset_key for p in first if p.kind == "short"} == {"C1", "C2", "C3", "C4", "C5"}
    assert {p.status for p in first} == {"draft"} and {p.visibility for p in first} == {"private"}
    again = planned(world)
    assert len(again) == len(first) and world.db.query(Publication).filter_by(case_id=world.case.id).count() == len(first)


def test_the_plan_times_come_from_the_farsi_schedule_in_utc(world):
    planned(world)
    film = pub(world, "youtube", "film", f"V{world.film.id}")
    assert film.scheduled_at.isoformat().startswith("2030-01-07T17:00:00")          # 20:30 in Tehran
    assert film.window_ends_at - film.scheduled_at == timedelta(minutes=60)
    from app.shortform.release_plan import load_config

    template = {(int(s["short"]), legacy): int(s["day"]) for s in load_config()["schedule_template"] for legacy in s["platforms"]}
    short = pub(world, "youtube", "short", "C1")
    assert short.scheduled_at.date() == START + timedelta(days=template[(1, "youtube_short")])


def test_a_language_without_a_schedule_has_no_plan(world):
    with pytest.raises(PublishingRefused, match="no publishing schedule"):
        world.registry.plan(world.case.id, "en")


def test_the_accounts_are_filled_from_the_existing_configuration_without_secrets(world):
    planned(world)
    accounts = {a.platform: a for a in world.db.query(PublicationAccount).filter_by(language="fa").all()}
    assert accounts["youtube"].external_id == "UC3bIVrg7dD6B9_JlSHduPLw" and accounts["youtube"].status == "connected"
    assert accounts["instagram"].external_id == "17841431693135313"
    assert accounts["facebook"].status == accounts["tiktok"].status == "not_connected"
    assert all(a.secret_ref == "" and not a.auto_eligible for a in accounts.values())


def test_a_new_render_voids_the_unpublished_publications_of_the_old_one(world):
    planned(world)
    old_key = f"V{world.film.id}"
    world.film.state = "archived"
    world.db.commit()
    shutil.copy(world.clips / "clip_C1.mp4", world.tmp / "newfilm.mp4")
    newer = Video(case_id=world.case.id, language="fa", mode="full", render_profile="publish", production_type="original", state="rendered", title="Newer",
                  file_path=storage.rel(world.tmp / "newfilm.mp4"))
    world.db.add(newer)
    world.db.commit()
    planned(world)
    assert pub(world, "youtube", "film", old_key).status == "voided"
    assert pub(world, "youtube", "film", f"V{newer.id}").status == "draft"


# --- approvals bound to a hash -------------------------------------------------------------------------------------------------------------------------------

def test_content_then_publication_approval_and_what_voids_them(world):
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    with pytest.raises(PublishingRefused, match="Approve the content first"):
        world.registry.approve_publication(short.id)
    world.registry.approve_content(short.id)
    approved = world.registry.approve_publication(short.id)
    assert approved.status == "publication_approved" and world.registry.approval_valid(approved) and approved.publication_hash
    # a settings change (the schedule) voids the publication approval only
    world.registry.update(short.id, scheduled_local="2030-01-09T21:00")
    short = pub(world, "youtube", "short", "C1")
    assert short.status == "voided" and short.content_approved_hash and short.publication_approved_hash is None
    assert [e.reason for e in events(world, "approval_voided")] == ["the links or settings changed"]
    again = world.registry.approve_publication(short.id)
    assert again.status == "publication_approved"
    # a caption change voids both
    world.registry.update(short.id, caption="Another caption")
    short = pub(world, "youtube", "short", "C1")
    assert short.status == "voided" and short.content_approved_hash is None and short.publication_approved_hash is None
    assert [e.reason for e in events(world, "approval_voided")][-1] == "the video, cover or caption changed"
    with pytest.raises(PublishingRefused, match="Approve the content first"):
        world.registry.approve_publication(short.id)


def test_a_changed_video_file_voids_the_approval(world):
    planned(world)
    short = pub(world, "tiktok", "short", "C2")
    world.registry.approve_content(short.id)
    world.registry.approve_publication(short.id)
    path = storage.resolve(short.video_path)
    shutil.copy(world.clips / "clip_C1.mp4", path)
    world.registry.refresh(short)
    world.db.commit()
    assert pub(world, "tiktok", "short", "C2").status == "voided" and any("video" in (e.reason or "") for e in events(world, "approval_voided"))


def test_an_approval_expires_after_the_configured_days(world):
    planned(world)
    short = pub(world, "facebook", "short", "C3")
    world.registry.approve_content(short.id)
    world.registry.approve_publication(short.id)
    days = publishing_config().approval.max_age_days
    world.clock["now"] += timedelta(days=days - 1)
    assert world.registry.approval_current(world.registry.refresh(pub(world, "facebook", "short", "C3")))
    world.clock["now"] += timedelta(days=2)
    world.registry.refresh(pub(world, "facebook", "short", "C3"))
    world.db.commit()
    short = pub(world, "facebook", "short", "C3")
    assert short.status == "expired" and not world.registry.approval_current(short) and events(world, "approval_expired")
    with pytest.raises(PublishingRefused):
        world.registry.schedule(short.id)


def test_revoking_an_approval_returns_to_draft(world):
    planned(world)
    short = pub(world, "youtube", "short", "C4")
    world.registry.approve_content(short.id)
    assert world.registry.revoke(short.id).status == "draft"
    with pytest.raises(PublishingRefused, match="no approval to withdraw"):
        world.registry.revoke(short.id)


def test_a_film_in_a_preview_render_is_not_refused_for_its_render_profile(world):
    planned(world)
    film = pub(world, "youtube", "film", f"V{world.film.id}")
    with pytest.raises(PublishingRefused) as refused:
        world.registry.approve_content(film.id)
    assert "profile" not in str(refused.value) and "no approved episode title" in str(refused.value)            # it is held back for its own reasons only


def test_a_film_that_meets_the_publish_rules_can_be_approved(world, monkeypatch):
    ready_film(monkeypatch)
    planned(world)
    film = pub(world, "facebook", "film", f"V{world.film.id}")
    assert world.registry.approve_content(film.id).status == "content_approved"


def test_a_short_that_fails_the_instagram_media_check_cannot_be_approved(world):
    _video(world.clips / "clip_C5.mp4", editlist=True)
    planned(world)
    reel = pub(world, "instagram", "short", "C5")
    with pytest.raises(PublishingRefused, match="Instagram media check: no edit list"):
        world.registry.approve_content(reel.id)
    assert world.registry.approve_content(pub(world, "tiktok", "short", "C5").id).status == "content_approved"      # TikTok has no media check here


def test_a_missing_video_file_blocks_approval(world):
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    (world.clips / "clip_C1.mp4").unlink()
    with pytest.raises(PublishingRefused, match="video file is missing"):
        world.registry.approve_content(short.id)


def test_editing_rules(world):
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    with pytest.raises(PublishingRefused, match="caption cannot be empty"):
        world.registry.update(short.id, caption="  ")
    with pytest.raises(PublishingRefused, match="(?i)visibility"):
        world.registry.update(short.id, visibility="secret")
    with pytest.raises(PublishingRefused, match="valid local date"):
        world.registry.update(short.id, scheduled_local="not a time")
    edited = world.registry.update(short.id, scheduled_local="2030-02-01T20:30")
    assert edited.scheduled_at.isoformat().startswith("2030-02-01T17:00:00") and world.registry.view(edited)["scheduled_local"] == "2030-02-01T20:30"
    short.status = "uploading"
    world.db.commit()
    with pytest.raises(PublishingRefused, match="cannot be edited"):
        world.registry.update(short.id, caption="x")


# --- the order of release and the routing links -----------------------------------------------------------------------------------------------------------------------

def test_a_short_waits_for_its_film_and_then_can_be_scheduled(world, monkeypatch):
    monkeypatch.setattr(world.registry.cfg.scheduling, "film_before_shorts", True)
    ready_film(monkeypatch)
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    world.registry.approve_content(short.id)
    world.registry.approve_publication(short.id)
    with pytest.raises(PublishingRefused, match="film is not published yet"):
        world.registry.schedule(short.id)
    link = world.db.query(RoutingLink).filter_by(publication_id=short.id, method="related_video").one()
    assert link.status == "manual_action_required" and link.url is None
    world.registry.record_film_published(world.film, url="https://www.youtube.com/watch?v=FILM1", published_at=None, episode_number=1)
    world.db.expire_all()
    link = world.db.query(RoutingLink).filter_by(publication_id=short.id, method="related_video").one()
    assert (link.status, link.url) == ("url_recorded", "https://www.youtube.com/watch?v=FILM1")
    short = pub(world, "youtube", "short", "C1")
    assert short.status == "voided"                           # the link it was approved with changed: it must be approved again
    world.registry.approve_content(short.id)
    world.registry.approve_publication(short.id)
    with pytest.raises(PublishingRefused, match="facebook/film film is not published yet"):                # its text points to the Facebook film too
        world.registry.schedule(short.id)
    facebook = pub(world, "facebook", "film", f"V{world.film.id}")
    facebook.status, facebook.remote_url = "published", "https://www.facebook.com/reel/FILM1/"
    world.db.commit()
    assert world.registry.schedule(pub(world, "youtube", "short", "C1").id).status == "scheduled"
    assert events(world, "scheduled")


def test_routing_statuses_keep_a_recorded_url_apart_from_a_verified_link(world, monkeypatch):
    ready_film(monkeypatch)
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    link = world.db.query(RoutingLink).filter_by(publication_id=short.id, method="related_video").one()
    with pytest.raises(PublishingRefused, match="no URL to record"):
        world.registry.update_routing(link.id, status="link_created")
    world.registry.record_film_published(world.film, url="https://www.youtube.com/watch?v=FILM1", published_at=None, episode_number=1)
    with pytest.raises(PublishingRefused, match="Create the link first"):
        world.registry.update_routing(link.id, status="clickability_verified", evidence_note="tapped it")
    world.registry.update_routing(link.id, status="link_created")
    with pytest.raises(PublishingRefused, match="needs a note"):
        world.registry.update_routing(link.id, status="clickability_verified")
    verified = world.registry.update_routing(link.id, status="clickability_verified", evidence_note="Tapped on the Short in the Shorts player on a phone.")
    assert verified.status == "clickability_verified" and verified.verified_by == "owner" and verified.verified_at
    with pytest.raises(PublishingRefused, match="Unknown routing status"):
        world.registry.update_routing(link.id, status="clickable")
    world.registry.record_film_published(world.film, url="https://www.youtube.com/watch?v=FILM2", published_at=None, episode_number=1)
    world.db.expire_all()
    changed = world.db.get(RoutingLink, link.id)
    assert changed.status == "url_recorded" and changed.verified_at is None and changed.url.endswith("FILM2")      # the earlier check was about another URL


def test_plain_text_calls_to_action_exist_for_instagram_and_tiktok_and_link_methods_are_manual(world):
    planned(world)
    methods = {(p, r.method) for p in ("instagram", "tiktok") for r in world.db.query(RoutingLink).join(Publication, Publication.id == RoutingLink.publication_id)
               .filter(Publication.case_id == world.case.id, Publication.platform == p, Publication.asset_key == "C1")}
    assert ("instagram", "plain_text_cta") in methods and ("instagram", "profile_link") in methods and ("tiktok", "plain_text_cta") in methods
    assert not any(m == "related_video" for p, m in methods)


# --- the film's URL and publication state: one writer -----------------------------------------------------------------------------------------------------------------

def test_record_film_published_writes_the_registry_and_mirrors_the_video(world):
    result = world.registry.record_film_published(world.film, url="https://youtu.be/AAA", published_at=None, episode_number=3)
    assert (result.state, result.youtube_url, result.episode_number) == ("published", "https://youtu.be/AAA", 3) and result.published_at
    row = pub(world, "youtube", "film", f"V{world.film.id}")
    assert (row.status, row.remote_url) == ("published", "https://youtu.be/AAA") and row.content_hash and not row.content_hash.startswith("legacy")
    published_at = result.published_at
    again = world.registry.record_film_published(world.film, url="https://youtu.be/BBB", published_at=None, episode_number=None)
    row = pub(world, "youtube", "film", f"V{world.film.id}")
    assert again.youtube_url == row.remote_url == "https://youtu.be/BBB" and again.published_at == published_at and again.episode_number == 3
    assert len(events(world, "film_published")) == 1 and len(events(world, "film_link_updated")) == 1


def test_the_publish_endpoint_goes_through_the_registry(world, client, monkeypatch):
    ready_film(monkeypatch)
    response = client.post(f"/api/films/{world.film.id}/publish", json={"youtube_url": "https://youtu.be/CCC"})
    assert response.status_code == 200 and response.json()["youtube_url"] == "https://youtu.be/CCC" and response.json()["state"] == "published"
    row = pub(world, "youtube", "film", f"V{world.film.id}")
    assert (row.status, row.remote_url) == ("published", "https://youtu.be/CCC")
    blocked = client.post(f"/api/films/{world.film.id}/publish", json={})
    assert blocked.status_code == 200          # an already published film is edited, never refused


def test_a_file_cannot_be_published_twice_on_the_same_account(world, monkeypatch):
    ready_film(monkeypatch)
    twin = Video(case_id=world.case.id, language="fa", mode="full", render_profile="publish", production_type="original", state="rendered", title="Twin",
                 file_path=world.film.file_path)
    world.db.add(twin)
    world.db.commit()
    world.registry.record_film_published(world.film, url="https://youtu.be/ONE", published_at=None, episode_number=1)
    with pytest.raises(PublishingRefused, match="already published"):
        world.registry.record_film_published(twin, url="https://youtu.be/TWO", published_at=None, episode_number=2)


# --- hashing -------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_file_hash_is_streamed_not_loaded(tmp_path, monkeypatch):
    big = tmp_path / "big.bin"
    with big.open("wb") as f:
        for _ in range(24):
            f.write(b"x" * 1024 * 1024)
    expected = hashlib.sha256(big.read_bytes()).hexdigest()
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("the whole file was read")))
    tracemalloc.start()
    digest = file_sha256(big)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert digest == expected and peak < 4 * 1024 * 1024


def test_the_hash_cache_reads_a_file_again_only_when_it_changed(world, monkeypatch):
    path = world.clips / "clip_C1.mp4"
    calls = []
    real = hashing.file_sha256
    monkeypatch.setattr(hashing, "file_sha256", lambda p: calls.append(p) or real(p))
    first = hashing.cached_file_sha256(world.db, path)
    assert hashing.cached_file_sha256(world.db, path) == first and len(calls) == 1
    assert world.db.get(FileHashCache, str(path.resolve())).sha256 == first
    shutil.copy(world.clips / "clip_C2.mp4", path)
    changed = hashing.cached_file_sha256(world.db, path)
    assert changed != first and len(calls) == 2 and changed == real(path)
    assert hashing.fresh_file_sha256(path) == changed and len(calls) == 3         # a live operation always reads the file


def test_the_approval_bundle_film_hash_streams_and_agrees_with_sha256(world, monkeypatch):
    from app.services import approval_bundles

    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("the whole file was read")))
    assert approval_bundles.film_hash(world.film, world.db) == approval_bundles.film_hash(world.film) == file_sha256(world.film_file)


# --- the migration keeps what was published ----------------------------------------------------------------------------------------------------------------------

def test_the_migration_copies_published_films_and_changes_nothing_in_videos(world):
    spec = importlib.util.spec_from_file_location("migration_0111", ROOT / "migrations" / "versions" / "0111_publication_registry.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    world.film.state, world.film.youtube_url, world.film.published_at = "published", "https://youtu.be/OLD", utc_now()
    world.db.commit()
    before = world.db.execute(text("select state, youtube_url, published_at from videos where id = :i"), {"i": world.film.id}).one()
    assert migration.copy_published_films(world.db.connection()) >= 1
    world.db.commit()
    row = pub(world, "youtube", "film", f"V{world.film.id}")
    assert (row.status, row.remote_url, row.content_hash) == ("published", "https://youtu.be/OLD", f"legacy-video-{world.film.id}")
    assert row.published_at is not None
    assert world.db.execute(text("select state, youtube_url, published_at from videos where id = :i"), {"i": world.film.id}).one() == before
    assert migration.copy_published_films(world.db.connection()) == 0           # a second run copies nothing


# --- the API and the Monitoring page -----------------------------------------------------------------------------------------------------------------------

def test_the_studio_api_end_to_end(world, client, monkeypatch):
    monkeypatch.setattr(world.registry.cfg.scheduling, "film_before_shorts", True)
    base = f"/api/cases/{world.case.id}/publishing/fa"
    before = client.get(base).json()
    assert before["planned"] is False and [a["key"] for a in before["assets"]] == [f"V{world.film.id}", "C1", "C2", "C3", "C4", "C5"]
    assert before["schedule"] == {"timezone": "Asia/Tehran", "publish_time_local": "20:30"} and before["approval_max_age_days"] == 7
    planned_view = client.post(f"{base}/plan", json={"start_date": START.isoformat()}).json()
    assert planned_view["planned"] is True and all(len(a["publications"]) == (2 if a["kind"] == "film" else 4) for a in planned_view["assets"])
    youtube = next(p for a in planned_view["assets"] if a["key"] == "C1" for p in a["publications"] if p["platform"] == "youtube")
    edited = client.patch(f"/api/publications/{youtube['id']}", json={"caption": "My caption", "scheduled_local": "2030-03-03T20:30"}).json()
    assert edited["caption"] == "My caption" and edited["scheduled_local"] == "2030-03-03T20:30"
    assert client.post(f"/api/publications/{youtube['id']}/publication-approval").status_code == 409
    assert youtube["content_approved"] is False
    approved_content = client.post(f"/api/publications/{youtube['id']}/content-approval").json()
    assert approved_content["status"] == "content_approved" and approved_content["content_approved"] is True
    approved = client.post(f"/api/publications/{youtube['id']}/publication-approval").json()
    assert approved["status"] == "publication_approved" and approved["approval_valid"] is True and approved["approval_expires_at"]
    refused = client.post(f"/api/publications/{youtube['id']}/schedule")
    assert refused.status_code == 409 and "film is not published yet" in refused.json()["detail"]["message"] and refused.json()["detail"]["reasons"]
    reloaded = client.get(base).json()
    again = next(p for a in reloaded["assets"] if a["key"] == "C1" for p in a["publications"] if p["platform"] == "youtube")
    assert (again["status"], again["caption"], again["scheduled_local"]) == ("publication_approved", "My caption", "2030-03-03T20:30")
    assert client.post(f"/api/publications/{youtube['id']}/revoke").json()["status"] == "draft"
    assert client.post(f"/api/cases/{world.case.id}/publishing/en/plan", json={}).status_code == 409
    assert client.get("/api/cases/999999/publishing/fa").status_code == 404
    assert client.post("/api/publications/999999/content-approval").status_code == 409


def test_the_routing_api(world, client, monkeypatch):
    ready_film(monkeypatch)
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    link = world.db.query(RoutingLink).filter_by(publication_id=short.id, method="related_video").one()
    world.registry.record_film_published(world.film, url="https://youtu.be/ROUTE", published_at=None, episode_number=1)
    created = client.patch(f"/api/routing-links/{link.id}", json={"status": "link_created", "evidence_note": "Set in YouTube Studio"})
    assert created.status_code == 200
    assert next(item for item in created.json()["links"] if item["id"] == link.id)["status"] == "link_created"
    assert client.patch(f"/api/routing-links/{link.id}", json={"status": "clickability_verified"}).status_code == 200
    assert client.patch(f"/api/routing-links/{link.id}", json={"status": "nonsense"}).status_code == 409


def test_publishing_findings_reach_the_monitoring_page(world, client):
    planned(world)
    short = pub(world, "youtube", "short", "C1")
    reel = pub(world, "tiktok", "short", "C2")
    world.registry.approve_content(short.id)
    world.registry.update(short.id, caption="Changed after approval")           # approval voided
    reel.status, reel.error = "failed", "The platform refused the upload."
    world.db.commit()
    case_issues = client.get(f"/api/cases/{world.case.id}/monitoring").json()["issues"]
    publishing = [i for i in case_issues if i["area"] == "publishing"]
    severity = {i["title"].split(":")[0]: i["severity"] for i in publishing}
    cfg = publishing_config().monitoring.events
    assert severity["Publishing failed"] == cfg["publication_failed"].severity.value == "problem"
    assert severity["Approval voided"] == cfg["approval_voided"].severity.value == "attention"
    assert any(i["title"].startswith("Publishing failed: tiktok short C2") and i["detail"] == "The platform refused the upload." for i in publishing)
    assert publishing and all(i["actions"][0]["target"] == f"/cases/{world.case.id}?tab=Publishing&language=fa" for i in publishing)
    app_issues = client.get("/api/monitoring").json()["issues"]
    mine = [i for i in app_issues if i["area"] == "publishing" and "Registry test" in i["title"]]
    assert any("Publishing failed" in i["title"] for i in mine) and client.get("/api/monitoring").json()["state"] == "problem"


def test_a_published_short_with_an_open_link_is_a_finding_and_a_scheduled_one_past_its_window_too(world):
    from app.publishing.monitoring import publishing_issues

    planned(world)
    done = pub(world, "instagram", "short", "C1")
    done.status = "published"
    world.db.commit()
    late = pub(world, "facebook", "short", "C2")
    late.status, late.scheduled_at, late.window_ends_at = "scheduled", utc_now() - timedelta(hours=3), utc_now() - timedelta(hours=2)
    world.db.commit()
    titles = [i.title for i in publishing_issues(world.db, world.case.id)]
    assert any(t.startswith("A link still needs doing: instagram short C1") for t in titles)
    assert any(t.startswith("Publication window missed: facebook short C2") for t in titles)


def test_a_public_youtube_short_without_its_related_video_is_a_finding_with_the_studio_address(world):
    from app.publishing.monitoring import publishing_issues

    planned(world)
    short = pub(world, "youtube", "short", "C1")
    short.status, short.remote_id = "published", "SHORTID1"
    world.db.commit()
    detail = next(i.detail for i in publishing_issues(world.db, world.case.id) if i.title.startswith("A link still needs doing: youtube short C1"))
    assert "related_video to youtube/film" in detail and "https://studio.youtube.com/video/SHORTID1/edit" in detail


# --- the release plan reads the one schedule source ----------------------------------------------------------------------------------------------------------------

def _release_world(world):
    from app.db.models import DocumentaryJob, RenderedShort, ShortCandidate

    job = DocumentaryJob(case_id=world.case.id, status="completed", mode="full", languages_json='["fa", "en"]')
    world.db.add(job)
    world.db.flush()
    candidate = ShortCandidate(case_id=world.case.id, film_job_id=job.id, stable_key="SFC_TEST", status="selected")
    world.db.add(candidate)
    world.db.flush()
    for language in ("fa", "en"):
        world.db.add(RenderedShort(case_id=world.case.id, candidate_id=candidate.id, film_job_id=job.id, language=language, status="done", video_path="x.mp4"))
    world.db.commit()
    return job


def test_the_release_plan_uses_the_publishing_schedule_per_language(world):
    from app.db.models import DocumentaryJob, RenderedShort, ShortCandidate
    from app.shortform import release_plan

    _release_world(world)
    document = release_plan.build_release_document(world.db, world.case.id, start_date=START)
    fa = [s for s in document["slots"] if s["language"] == "fa"]
    en = [s for s in document["slots"] if s["language"] == "en"]
    assert fa and en
    assert all(s["publish_at"].endswith("+03:30") and "T20:30:00" in s["publish_at"] for s in fa)          # 20:30 in Tehran, not 18:00 Berlin
    assert all(s["publish_at"] is None and s["status"] == "language_not_scheduled" for s in en)
    assert document["timezone"] == "Asia/Tehran" and document["publish_time_local"] == "20:30"
    assert document["schedules"] == {"fa": {"timezone": "Asia/Tehran", "publish_time_local": "20:30", "start_date": START.isoformat()}}
    stored = release_plan.store_draft(world.db, world.case.id, document)
    assert json.loads(stored.plan_json)["slots"] == document["slots"]                                       # a saved plan keeps its times as computed
    world.db.query(RenderedShort).filter_by(case_id=world.case.id).delete()
    world.db.query(ShortCandidate).filter_by(case_id=world.case.id).delete()
    world.db.query(DocumentaryJob).filter_by(case_id=world.case.id).delete()
    from app.db.models import ShortFormPublishingPlan

    world.db.query(ShortFormPublishingPlan).filter_by(case_id=world.case.id).delete()
    world.db.commit()


def test_a_release_plan_start_date_in_the_past_is_still_refused(world):
    from app.db.models import DocumentaryJob, RenderedShort, ShortCandidate
    from app.shortform import release_plan

    _release_world(world)
    try:
        with pytest.raises(ValueError, match="cannot be in the past"):
            release_plan.build_release_document(world.db, world.case.id, start_date=date(2001, 1, 1))
    finally:
        world.db.query(RenderedShort).filter_by(case_id=world.case.id).delete()
        world.db.query(ShortCandidate).filter_by(case_id=world.case.id).delete()
        world.db.query(DocumentaryJob).filter_by(case_id=world.case.id).delete()
        world.db.commit()


def test_each_film_address_goes_into_the_shorts_the_routing_config_names_and_a_public_short_is_only_offered_its_new_text(world):
    planned(world)
    youtube_film = pub(world, "youtube", "film", f"V{world.film.id}")
    facebook_film = pub(world, "facebook", "film", f"V{world.film.id}")
    youtube_film.remote_url, facebook_film.remote_url = "https://www.youtube.com/watch?v=FILM123", "https://www.facebook.com/page/videos/77/"
    world.db.commit()
    public = pub(world, "instagram", "short", "C2")
    public.status, public.remote_id = "published", "IGMEDIA"
    world.db.commit()
    world.registry.link_shorts_to_film(youtube_film)
    world.registry.link_shorts_to_film(facebook_film)
    rows = lambda platform: pub(world, platform, "short", "C1").caption.split("\n")                           # noqa: E731
    for platform in ("youtube", "facebook", "instagram"):                                                              # every Short and Reel points to both films, in its first two rows
        assert rows(platform)[:2] == [youtube_film.remote_url, facebook_film.remote_url]
    assert pub(world, "tiktok", "short", "C1").caption.count("http") == 0                                         # a platform the config does not name is left alone
    offered = world.registry.published_shorts_with_film_addresses(world.case.id, "fa", "instagram")
    assert [(p.id, youtube_film.remote_url in text and facebook_film.remote_url in text) for p, text in offered] == [(public.id, True)]
    assert pub(world, "instagram", "short", "C2").caption.count("http") == 0                                      # offered, not written


def test_a_films_youtube_address_is_put_into_the_text_of_its_editable_shorts_only_and_never_twice(world):
    planned(world)
    film = pub(world, "youtube", "film", f"V{world.film.id}")
    film.remote_url = "https://www.youtube.com/watch?v=FILM123"
    world.db.commit()
    first, second = pub(world, "youtube", "short", "C1"), pub(world, "youtube", "short", "C2")
    first_id, second.status = first.id, "published"
    world.db.commit()
    tiktok = pub(world, "tiktok", "short", "C1").caption
    changed = world.registry.link_shorts_to_film(film)
    assert first_id in changed and second.id not in changed                                                    # a public Short's text is not edited here
    assert film.remote_url in pub(world, "youtube", "short", "C1").caption and film.remote_url not in pub(world, "youtube", "short", "C2").caption
    assert pub(world, "tiktok", "short", "C1").caption == tiktok                                               # a platform the config does not name is not touched
    assert world.registry.link_shorts_to_film(film) == []                                                       # nothing is added twice
    film.remote_url = None
    assert world.registry.link_shorts_to_film(film) == []



def test_every_shorts_delivery_copy_is_prepared_with_its_approved_reel_cover_and_made_again_only_when_something_changed(world):
    from app.db.models import CoverVariant
    from app.shortform.delivery import prepare_shorts

    for key in ("C2", "C3", "C4", "C5"):
        (world.clips / f"clip_{key}.mp4").unlink()                                             # a small test: one clip is enough
    picture = world.tmp / "reel.jpg"
    Image.new("RGB", (1080, 1920), (200, 20, 20)).save(picture)
    first = prepare_shorts(world.db, world.case.id, "fa")
    assert [(r["key"], r["cover"], r["made_now"]) for r in first] == [("C1", None, True)]       # no approved cover: a copy that opens with the clip
    world.db.add(CoverVariant(case_id=world.case.id, language="fa", cover_key="C1", shape="reel", version=1, status="approved", title="t", highlight="h", subject_name="n", file_path=str(picture),
                              file_hash="abc", width=1080, height=1920, elements_json="{}", problems_json="[]", evidence_json="{}", approved_by="owner"))
    world.db.commit()
    second = prepare_shorts(world.db, world.case.id, "fa")
    assert second[0]["cover"].startswith("reel:") and second[0]["made_now"]                     # the cover came: the copy is made again, with it
    third = prepare_shorts(world.db, world.case.id, "fa")
    assert third[0]["made_now"] is False and third[0]["file"] == "delivery_C1.mp4"              # nothing changed: nothing is made


def test_planning_makes_the_delivery_copies_with_the_approved_reel_cover_and_the_publications_send_them(world, monkeypatch):
    from app.db.models import CoverVariant

    monkeypatch.setattr(world.registry.cfg.plan, "make_delivery_copies", True)
    for key in ("C2", "C3", "C4", "C5"):
        (world.clips / f"clip_{key}.mp4").unlink()
    picture = world.tmp / "reel.jpg"
    Image.new("RGB", (1080, 1920), (200, 20, 20)).save(picture)
    world.db.add(CoverVariant(case_id=world.case.id, language="fa", cover_key="C1", shape="reel", version=1, status="approved", title="t", highlight="h", subject_name="n", file_path=str(picture),
                              file_hash="abc", width=1080, height=1920, elements_json="{}", problems_json="[]", evidence_json="{}", approved_by="owner"))
    world.db.commit()
    planned(world)
    assert (world.clips / "delivery_C1.mp4").is_file()
    assert {pub(world, platform, "short", "C1").video_path.rsplit("/", 1)[-1] for platform in ("youtube", "facebook", "instagram", "tiktok")} == {"delivery_C1.mp4"}
    monkeypatch.setattr("app.shortform.delivery.prepare_shorts", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    planned(world)                                                                                          # a copy that cannot be made never stops the plan


def test_a_shorts_caption_is_planned_with_the_film_rows_on_top_whether_or_not_the_films_have_addresses(world):
    from app.youtube_publishing.uploader import compose_description

    planned(world)
    rows = lambda platform: pub(world, platform, "short", "C1").caption.split("\n")                           # noqa: E731
    for platform in ("youtube", "facebook", "instagram"):
        assert rows(platform)[:3] == ["{{film:youtube}}", "{{film:facebook}}", ""]
    assert pub(world, "tiktok", "short", "C1").caption.count("{{film") == 0                                      # a platform the config does not name has no film rows
    assert "{{film" not in compose_description(pub(world, "facebook", "short", "C1"))                         # a reserved row is never sent
    youtube_film = pub(world, "youtube", "film", f"V{world.film.id}")
    youtube_film.remote_url = "https://www.youtube.com/watch?v=FILM123"
    world.db.commit()
    world.registry.link_shorts_to_film(youtube_film)
    assert rows("facebook")[:3] == ["https://www.youtube.com/watch?v=FILM123", "{{film:facebook}}", ""] and rows("youtube")[:2] == ["https://www.youtube.com/watch?v=FILM123", "{{film:facebook}}"]
    assert any("A film has no address yet" in w for w in world.registry.warnings(pub(world, "facebook", "short", "C1")))      # the Reel would go out without its second link
    assert not any("A film has no address yet" in w for w in world.registry.warnings(pub(world, "tiktok", "short", "C1")))
    planned(world)                                                                                          # planning again changes nothing that is already in place
    assert rows("facebook")[:2] == ["https://www.youtube.com/watch?v=FILM123", "{{film:facebook}}"] and pub(world, "facebook", "short", "C1").caption.count("film123".upper()) == 1


def test_every_language_has_its_bio_line_and_only_instagram_reels_carry_it_before_the_music_credit(world):
    from app.youtube_publishing.uploader import compose_description

    config = world.registry.cfg.routing
    assert set(config.bio_link_note) == {"fa", "en", "de", "ar", "es", "it", "fr", "pt", "tr"} and all(config.bio_link_note.values())
    planned(world)
    note = config.bio_link_note["fa"]
    instagram = pub(world, "instagram", "short", "C1").caption
    assert instagram.count(note) == 1 and instagram.split("\n\n")[0].startswith("{{film:youtube}}") and instagram.endswith(note)          # this test caption holds its credit inside one line, so the note ends the text
    assert all(note not in pub(world, platform, "short", "C1").caption for platform in ("youtube", "facebook", "tiktok"))
    assert note in compose_description(pub(world, "instagram", "short", "C1"))                                  # it is sent, unlike a reserved row
    planned(world)
    assert pub(world, "instagram", "short", "C1").caption == instagram                                         # planning again changes nothing


def test_a_short_waits_for_every_film_whose_address_is_in_its_text_unless_the_language_has_no_account_there(world, monkeypatch):
    from app.db.models import PublicationAccount

    monkeypatch.setattr(world.registry.cfg.scheduling, "film_before_shorts", True)
    planned(world)
    instagram = pub(world, "instagram", "short", "C1")
    assert [b.split(" film")[0].removeprefix("The ") for b in world.registry.film_blockers(instagram)] == ["youtube/film", "facebook/film"]
    assert world.registry.film_blockers(pub(world, "youtube", "film", f"V{world.film.id}")) == []                           # a film waits for nothing
    for platform in ("youtube", "facebook"):
        film = pub(world, platform, "film", f"V{world.film.id}")
        film.status, film.remote_url = "published", f"https://example.test/{platform}/1"
        world.db.commit()
        assert len(world.registry.film_blockers(pub(world, "instagram", "short", "C1"))) == (1 if platform == "youtube" else 0)
    assert world.registry.schedule_blockers(pub(world, "youtube", "short", "C1")) in ([], ["No publication time is set."])      # both films are out: nothing is left to wait for
    film = pub(world, "facebook", "film", f"V{world.film.id}")
    film.status, film.remote_url = "draft", None
    world.db.commit()
    assert len(world.registry.film_blockers(pub(world, "instagram", "short", "C1"))) == 1 and "facebook/film" in world.registry.film_blockers(pub(world, "instagram", "short", "C1"))[0]
    account = world.db.query(PublicationAccount).filter_by(platform="facebook", language="fa").one()
    page_id = account.external_id
    account.external_id = ""                                                                                              # a language with no Facebook Page has no Facebook film to wait for
    world.db.commit()
    try:
        assert world.registry.film_blockers(pub(world, "instagram", "short", "C1")) == []
    finally:
        account.external_id = page_id                                                                                     # the account row is shared by every test: put it back
        world.db.commit()

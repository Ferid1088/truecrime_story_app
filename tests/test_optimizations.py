"""Behaviour added by the optimization pass (no network)."""

import asyncio
import json

import pytest

from app.core.ai_config import ai_config
from app.core.tasks import running, spawn
from app.db.models import Case, EpisodeIdentity, ResearchJob, Video
from app.documentary import jobs as J
from app.identity import titles as T
from app.lifecycle import videos as V
from app.lifecycle.status import set_resolution


def _case(db, name, status="UNSOLVED"):
    c = Case(canonical_title=name, slug=name.lower().replace(" ", "-") + "-opt")
    db.add(c)
    db.flush()
    set_resolution(db, c, status, changed_by="user", reason="t", commit=False)
    db.commit()
    return c


def _video(db, case, **kw):
    v = Video(case_id=case.id, language="de", title="Der Kreis", mode=kw.pop("mode", "full"),
              render_profile=kw.pop("render_profile", "publish"), **kw)
    db.add(v)
    db.commit()
    return v


def test_spawn_keeps_the_task_until_it_is_done():
    async def main():
        done = asyncio.Event()

        async def work():
            await asyncio.sleep(0)
            done.set()

        t = spawn(work(), name="t")
        assert running() >= 1
        await done.wait()
        await asyncio.sleep(0)
        return t.done()

    assert asyncio.run(main())


def test_publish_blockers(db_session):
    case = _case(db_session, "Opt publish")
    T.sync_identity(db_session, case, "de", title="Der leere Kreis")
    db_session.commit()
    pilot = _video(db_session, case, mode="pilot")
    preview = _video(db_session, case, render_profile="preview")
    full = _video(db_session, case)
    assert any("pilot" in b for b in V.publish_blockers(db_session, pilot))
    assert any("preview profile" in b for b in V.publish_blockers(db_session, preview))
    assert V.publish_blockers(db_session, full) == ["no approved episode title (Naming tab)"]
    ident = T.get_identity(db_session, case.id, "de")
    ident.title_approved = True
    db_session.commit()
    assert V.publish_blockers(db_session, full) == []
    set_resolution(db_session, case, "UNKNOWN", changed_by="user", reason="t")
    assert any("no public status" in b for b in V.publish_blockers(db_session, full))


def test_publish_api_refuses_then_allows_with_force(client, db_session):
    case = _case(db_session, "Opt publish api")
    video = _video(db_session, case, mode="pilot")
    r = client.post(f"/api/films/{video.id}/publish", json={})
    assert r.status_code == 409 and r.json()["detail"]["blockers"]
    ok = client.post(f"/api/films/{video.id}/publish", json={"force": True})
    assert ok.status_code == 200
    db_session.refresh(case)
    assert case.status == "published"


def test_unknown_status_has_no_public_title_and_followup_uses_the_same_shape(db_session):
    case = _case(db_session, "Opt titles", status="STATUS_UNDER_REVIEW")
    assert V.youtube_title(case, "en", "The Vanishing Circle") == ("", "needs_status")
    title, rule = V.youtube_title(case, "de", "x", production_type="follow_up")
    assert rule == "follow_up" and title.endswith("(Gelöst) | Fallspur")
    assert not title.startswith("GELÖST:")


def test_approved_title_wins_over_the_story_title(db_session):
    case = _case(db_session, "Opt approved")
    assert T.approved_title(db_session, case.id, "de") is None
    ident = T.sync_identity(db_session, case, "de", title="Entwurf aus der Geschichte")
    assert T.approved_title(db_session, case.id, "de") is None      # a draft is not approved
    ident.title_approved = True
    db_session.commit()
    assert T.approved_title(db_session, case.id, "de") == "Entwurf aus der Geschichte"
    T.sync_identity(db_session, case, "de", title="Anderer Titel")  # a change needs a new approval
    assert T.approved_title(db_session, case.id, "de") is None


def test_case_uid_is_unique_even_on_a_collision(db_session, monkeypatch):
    first = _case(db_session, "Opt uid one")
    from app import utils
    from app.db import models

    seq = iter([first.case_uid, first.case_uid, "CASE_ffffff"])
    monkeypatch.setattr(models, "new_case_uid", lambda: next(seq))
    second = Case(canonical_title="Opt uid two", slug="opt-uid-two", case_uid=first.case_uid)
    db_session.add(second)
    db_session.commit()
    assert second.case_uid == "CASE_ffffff" and utils.new_case_uid().startswith("CASE_")


def test_ingesting_job_is_not_seen_as_completed_and_not_reentered(db_session, monkeypatch):
    from app.services import research_jobs as RJ

    job = ResearchJob(job_type="research", status="running", external_job_id="tce-x", provider="truecrime")
    db_session.add(job)
    db_session.commit()
    RJ._INGESTING[job.id] = __import__("time").monotonic()

    class Boom:
        async def poll(self, *_):
            raise AssertionError("must not poll while ingesting")

    monkeypatch.setattr(RJ, "_provider_for", lambda j: Boom())
    out = asyncio.run(RJ.poll_job(db_session, job))
    assert out.status == "running"
    RJ._INGESTING.pop(job.id)


def test_list_poll_does_not_ingest(db_session, monkeypatch):
    from app.providers.base import ProviderJob
    from app.services import research_jobs as RJ

    job = ResearchJob(job_type="research", status="running", external_job_id="tce-y", provider="truecrime")
    db_session.add(job)
    db_session.commit()

    class Done:
        async def poll(self, *_):
            return ProviderJob(external_id="tce-y", status="completed", result={"sources": []})

    monkeypatch.setattr(RJ, "_provider_for", lambda j: Done())
    out = asyncio.run(RJ.poll_job(db_session, job, ingest=False))
    assert out.status == "running" and out.id not in RJ._INGESTING


def test_tts_budget_and_host_stages():
    assert J.tts_budget_left({"en": 500}, 0)
    assert J.tts_budget_left({"en": 500}, 1000)
    assert not J.tts_budget_left({"en": 600, "de": 400}, 1000)


def test_pick_master_requires_an_english_master(client, db_session):
    from app.db.models import StoryVersion

    case = _case(db_session, "Opt master")
    direct = StoryVersion(case_id=case.id, version=1, narrative_angle="{}", story_text="x",
                          engagement_score=1.0, kind="direct", language="en")
    db_session.add(direct)
    db_session.commit()
    r = client.post(f"/api/cases/{case.id}/documentary/jobs",
                    json={"languages": ["en"], "master_version_id": direct.id})
    assert r.status_code in (409, 422)


def test_legacy_search_paths_are_gone():
    import importlib.util

    assert importlib.util.find_spec("app.services.search") is None
    assert importlib.util.find_spec("app.agents.topic_discovery") is None
    assert "tavily_api_key" not in __import__("app.core.config", fromlist=["x"]).Settings.model_fields


def test_case_status_follows_production(db_session):
    case = _case(db_session, "Opt state")
    case.status = "story_ready"
    db_session.commit()
    J._set_case_state(db_session, case.id, "producing")
    db_session.refresh(case)
    assert case.status == "producing"
    case.status = "archived"
    db_session.commit()
    J._set_case_state(db_session, case.id, "rendered")
    db_session.refresh(case)
    assert case.status == "archived"             # fixed states are never overwritten

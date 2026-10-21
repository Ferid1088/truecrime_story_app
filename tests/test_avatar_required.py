"""Owner decision 2026-10-22: the avatar is required only for the FINAL version (supersedes
2026-10-21's "no film is built or rendered without it"). A2: script building and rendering (pilot
or full) never need an approved avatar — the draft is simply stored with `avatar_used: false`.
A3: publishing (app.lifecycle.videos.publish_blockers) and the autopilot's publish phase
(app.autopilot.runner._publish_inputs) both refuse without one, with a plain, non-blocking-to-
other-languages "needs you" entry in the autopilot's case."""

from pathlib import Path

from factories import make_story_version

from app.core.ai_config import ai_config
from app.db.models import Case, ProductionScript, Video
from app.documentary.production import host_timeline as HT
from app.lifecycle import videos as V
from app.utils import slugify

ROOT = Path(__file__).resolve().parent.parent


def _case(db, title="Avatar Gate Case"):
    import uuid

    case = Case(canonical_title=title, slug=slugify(title + uuid.uuid4().hex[:8]), language="en")
    db.add(case)
    db.commit()
    return case


def _script(db, case, *, avatar_used: bool, status="composed") -> ProductionScript:
    import json

    sv = make_story_version(db, case)
    ps = ProductionScript(case_id=case.id, story_version_id=sv.id, language="en", mode="full",
                          status=status, duration_seconds=600.0,
                          script_json=json.dumps({"avatar_used": avatar_used}))
    db.add(ps)
    db.commit()
    return ps


def _video(db, case, **kw):
    v = Video(case_id=case.id, language=kw.pop("language", "en"), mode=kw.pop("mode", "full"),
              render_profile=kw.pop("render_profile", "publish"), **kw)
    db.add(v)
    db.commit()
    return v


# -- A1: both old enforcement points are gone from script building and render ------------------


def test_the_old_hard_gate_is_gone_from_script_building_and_render():
    script_src = (ROOT / "app/documentary/production/script.py").read_text()
    chain_src = (ROOT / "app/documentary/pipeline/language.py").read_text()
    assert "require_avatar" not in script_src
    assert "require_avatar" not in chain_src
    assert not hasattr(HT, "require_avatar") and not hasattr(HT, "AvatarRequired")
    # A2: the script instead stamps the one flag the publish-time gate reads back
    assert 'script["avatar_used"] = bool(host)' in script_src


# -- A2: avatar_present() reads the stamped flag back, nothing more -----------------------------


def test_avatar_present_reads_the_stamped_flag(db_session):
    case = _case(db_session)
    with_avatar = _script(db_session, case, avatar_used=True)
    without = _script(db_session, case, avatar_used=False)
    assert HT.avatar_present(db_session, with_avatar.id) is True
    assert HT.avatar_present(db_session, without.id) is False


def test_avatar_present_is_false_with_no_script_at_all(db_session):
    assert HT.avatar_present(db_session, None) is False
    assert HT.avatar_present(db_session, 999999) is False


# -- A3: publish_blockers blocks without an avatar, allows with one -----------------------------


def test_publish_is_blocked_without_an_avatar_and_allowed_with_one(db_session):
    case = _case(db_session)
    from app.identity import titles as T
    from app.lifecycle.status import set_resolution

    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="t", commit=False)
    db_session.commit()
    T.sync_identity(db_session, case, "en", title="The Empty Circle")
    ident = T.get_identity(db_session, case.id, "en")
    ident.title_approved = True
    db_session.commit()

    draft = _script(db_session, case, avatar_used=False)
    final = _script(db_session, case, avatar_used=True)
    no_avatar_video = _video(db_session, case, production_script_id=draft.id)
    ready_video = _video(db_session, case, production_script_id=final.id)

    assert V.publish_blockers(db_session, no_avatar_video) == [
        "no approved avatar look for this language — approve one in Host Studio "
        "before this film can publish"]
    assert V.publish_blockers(db_session, ready_video) == []


def test_a_video_with_no_linked_script_is_not_judged_either_way(db_session):
    """A video with no production_script_id (legacy data, or a test fixture that never linked
    one) carries no fact to read the flag from — publish_blockers never invents one."""
    case = _case(db_session)
    from app.identity import titles as T
    from app.lifecycle.status import set_resolution

    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="t", commit=False)
    db_session.commit()
    T.sync_identity(db_session, case, "en", title="The Empty Circle")
    ident = T.get_identity(db_session, case.id, "en")
    ident.title_approved = True
    db_session.commit()
    video = _video(db_session, case)
    assert V.publish_blockers(db_session, video) == []


def test_video_dict_carries_the_draft_no_avatar_fact_for_the_app_to_show(db_session):
    """A2: "Draft, no avatar" (case page, run page) is a plain field on the shared video
    serialization, true/false when known, null when there is nothing to read it from."""
    case = _case(db_session)
    draft = _script(db_session, case, avatar_used=False)
    final = _script(db_session, case, avatar_used=True)
    no_avatar_video = _video(db_session, case, production_script_id=draft.id)
    ready_video = _video(db_session, case, production_script_id=final.id)
    legacy_video = _video(db_session, case)
    assert V.video_dict(no_avatar_video)["avatar_used"] is False
    assert V.video_dict(ready_video)["avatar_used"] is True
    assert V.video_dict(legacy_video)["avatar_used"] is None


def test_the_rule_can_still_be_turned_off_entirely(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.host, "required_for_film", False)
    case = _case(db_session)
    from app.identity import titles as T
    from app.lifecycle.status import set_resolution

    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="t", commit=False)
    db_session.commit()
    T.sync_identity(db_session, case, "en", title="The Empty Circle")
    ident = T.get_identity(db_session, case.id, "en")
    ident.title_approved = True
    db_session.commit()
    draft = _script(db_session, case, avatar_used=False)
    video = _video(db_session, case, production_script_id=draft.id)
    assert V.publish_blockers(db_session, video) == []

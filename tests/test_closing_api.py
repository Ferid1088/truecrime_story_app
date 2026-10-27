import pytest

from app.core.ai_config import ai_config
from app.db.models import AppSetting, Case, ClosingClip, SharedReelAsset
from app.shortform import closing_api as API
from app.shortform import closing_library as CL
from tests.test_closing_library import LANG, FakeRenderer, look


@pytest.fixture(autouse=True)
def isolate(db_session, monkeypatch, tmp_path):
    cfg = ai_config.shorts_host_led
    monkeypatch.setitem(cfg.outro_sentence, LANG, "Follow us.")
    monkeypatch.setitem(cfg.identity_line, LANG, "I'm Fereidoun.")
    monkeypatch.setitem(ai_config.voice.languages, LANG, ai_config.voice.languages["en"])
    monkeypatch.setitem(ai_config.channels, LANG, ai_config.channels["en"])
    monkeypatch.setattr(CL, "ROOT", tmp_path)
    renderer = FakeRenderer()
    monkeypatch.setattr(API, "get_renderer", lambda: renderer)
    monkeypatch.setattr(CL, "approved_looks",
                        lambda db, language: [look(1, "A"), look(2, "B")] if language == LANG else [])
    for model, field in ((ClosingClip, "language"), (SharedReelAsset, "language")):
        db_session.query(model).filter(getattr(model, field) == LANG).delete()
    db_session.query(AppSetting).filter_by(key=CL.SETTINGS_KEY).delete()
    db_session.commit()
    yield renderer
    for model in (ClosingClip, SharedReelAsset):
        db_session.query(model).filter(model.language == LANG).delete()
    db_session.query(AppSetting).filter_by(key=CL.SETTINGS_KEY).delete()
    db_session.commit()


@pytest.fixture
def case_id(db_session):
    case = Case(canonical_title="Closing API", slug="closing-api-test", language="en")
    db_session.add(case)
    db_session.commit()
    yield case.id
    db_session.delete(case)
    db_session.commit()


def test_the_overview_shows_the_switches_and_the_library(client):
    body = client.get("/api/closing-clips").json()
    assert body["settings"] == {"use_stored": True, "create_new": True}
    row = body["languages"][LANG]
    assert row["variants"] == 0 and row["approved_looks"] == 2 and row["missing"] == 2
    assert row["sentences"] == ["Follow us.", "I'm Fereidoun."]
    assert row["estimate_per_variant"]["total_eur"] > 0


def test_the_switches_persist_and_apply_at_once(client, db_session):
    assert client.put("/api/closing-clips/settings", json={"use_stored": False}).json() == {
        "use_stored": False, "create_new": True}
    assert client.get("/api/closing-clips").json()["settings"]["use_stored"] is False
    assert CL.ClosingSettings(db_session).use_stored is False
    client.put("/api/closing-clips/settings", json={"create_new": False})
    assert CL.ClosingSettings(db_session).as_dict() == {"use_stored": False, "create_new": False}


def test_create_missing_prints_the_estimate_and_renders_nothing_until_confirmed(client, isolate, case_id):
    got = client.post("/api/closing-clips/create-missing", json={"language": LANG, "case_id": case_id})
    body = got.json()
    assert body["created"] == [] and body["estimate"]["variants"] == 2
    assert body["estimate"]["total_eur"] == round(2 * body["estimate"]["per_variant"]["total_eur"], 4)
    assert isolate.calls == []


def test_create_missing_renders_after_the_click_and_the_table_shows_it(client, isolate, case_id):
    body = client.post("/api/closing-clips/create-missing",
                       json={"language": LANG, "case_id": case_id, "confirm": True}).json()
    assert len(body["created"]) == 2 and body["actual_eur"] == 0.68
    assert len(isolate.calls) == 2
    row = client.get("/api/closing-clips").json()["languages"][LANG]
    assert row["variants"] == 2 and row["missing"] == 0
    assert {c["outfit"] for c in row["clips"]} == {"A", "B"}


def test_create_missing_is_refused_when_creation_is_off(client, isolate, case_id):
    client.put("/api/closing-clips/settings", json={"create_new": False})
    got = client.post("/api/closing-clips/create-missing",
                      json={"language": LANG, "case_id": case_id, "confirm": True})
    assert got.status_code == 409 and isolate.calls == []


def test_create_missing_skips_cleanly_with_no_approved_avatar_look(client, isolate, case_id, caplog):
    """A4: a film/language with no approved avatar look never attempts a host-led closing clip
    (`CL.approved_looks` returns [] for "de" per the `isolate` fixture) — it is skipped with one
    clear, honest reason, not a silent "0 variants"."""
    import logging

    with caplog.at_level(logging.INFO):
        got = client.post("/api/closing-clips/create-missing",
                          json={"language": "de", "case_id": case_id, "confirm": True})
    body = got.json()
    assert got.status_code == 200
    assert body["created"] == [] and body["skipped_reason"] == "no approved avatar look for de"
    assert any("no approved avatar look" in r.message for r in caplog.records)
    assert isolate.calls == []


def test_an_unknown_language_or_case_is_refused(client, case_id):
    assert client.post("/api/closing-clips/create-missing",
                       json={"language": "xx", "case_id": case_id}).status_code == 422
    assert client.post("/api/closing-clips/create-missing",
                       json={"language": LANG, "case_id": 10**9}).status_code == 404


def test_a_provider_failure_is_a_readable_502_and_stores_nothing(client, isolate, case_id, db_session, monkeypatch):
    from app.providers.avatar.base import AvatarProviderError

    async def refuse(*args, **kwargs):
        raise AvatarProviderError("job_failed", "Insufficient credit")

    monkeypatch.setattr(isolate, "render", refuse)
    got = client.post("/api/closing-clips/create-missing",
                      json={"language": LANG, "case_id": case_id, "confirm": True})
    assert got.status_code == 502 and "Insufficient credit" in got.json()["detail"]
    assert db_session.query(ClosingClip).filter_by(language=LANG).count() == 0


def test_the_persian_and_arabic_wording_is_served_in_its_own_script_in_the_configured_order(client):
    languages = client.get("/api/closing-clips").json()["languages"]
    assert languages["fa"]["sentences"][-1] == "من فریدونم و اینجا داستان‌های جنایی واقعی رو واست تعریف می‌کنم."
    assert languages["ar"]["sentences"][-1] == "أنا فريدون، وهنا أروي لك قصص الجرائم الحقيقية."
    assert languages["fa"]["sentences"][0] == ai_config.shorts_host_led.outro_sentence["fa"]


def _without_setup(monkeypatch, language="es"):
    from app.core.ai_config import ai_config

    monkeypatch.delitem(ai_config.voice.languages, language)
    monkeypatch.delitem(ai_config.shorts_host_led.identity_line, language)
    monkeypatch.delitem(ai_config.shorts_host_led.outro_sentence, language)


def test_a_channel_without_voice_or_spoken_wording_is_listed_as_not_configured_instead_of_breaking_the_page(
        client, monkeypatch):
    _without_setup(monkeypatch)
    body = client.get("/api/closing-clips")
    assert body.status_code == 200
    rows = body.json()["languages"]
    assert rows["es"]["not_configured"] == ["voice", "identity line", "outro sentence"]
    assert rows["es"]["variants"] == 0 and rows["es"]["estimate_per_variant"]["total_eur"] == 0
    assert "not_configured" not in rows[LANG] and "not_configured" not in rows["tr"]


def test_no_closing_clip_is_made_for_a_channel_that_lacks_its_voice_or_wording(client, case_id, monkeypatch):
    _without_setup(monkeypatch)
    got = client.post("/api/closing-clips/create-missing", json={"language": "es", "case_id": case_id, "confirm": True})
    assert got.status_code == 409 and "voice" in got.json()["detail"]


def test_spanish_and_turkish_have_their_voice_and_closing_wording(client):
    rows = client.get("/api/closing-clips").json()["languages"]
    for language in ("es", "tr"):
        assert "not_configured" not in rows[language] and len(rows[language]["sentences"]) == 2

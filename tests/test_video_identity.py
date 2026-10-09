"""M1 of Master_Task_Case_Naming_Identity: channels, public status labels,
case_uid, EpisodeIdentity and the YouTube title builder (no network)."""

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, CaseStatusHistory
from app.identity import titles as T
from app.lifecycle.status import set_resolution


def _case(db, title, status="UNSOLVED"):
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-") + "-ident")
    db.add(case)
    db.flush()
    set_resolution(db, case, status, changed_by="user", reason="test", confidence=0.97,
                   sources=[{"id": "SRC_12"}, {"id": "SRC_44"}], commit=False)
    db.commit()
    return case


def test_public_status_only_solved_and_unsolved():
    assert T.public_status("SOLVED") == "solved"
    assert T.public_status("UNSOLVED") == "unsolved"
    for s in ("UNKNOWN", "STATUS_UNDER_REVIEW", None, ""):
        assert T.public_status(s) is None
        assert T.status_label(s, "en") is None


def test_unknown_never_becomes_unsolved_title():
    with pytest.raises(T.NoPublicStatus):
        T.build_youtube_title("The Vanishing Circle", "UNKNOWN", "en")


def test_labels_exact_in_all_languages():
    assert T.status_label("SOLVED", "en") == "Solved"
    assert T.status_label("UNSOLVED", "en") == "Unsolved"
    assert T.status_label("SOLVED", "de") == "Gelöst"
    assert T.status_label("UNSOLVED", "de") == "Ungelöst"
    assert T.status_label("SOLVED", "fa") == "حل‌شده"
    assert T.status_label("UNSOLVED", "fa") == "حل‌نشده"
    assert T.status_label("SOLVED", "ar") == "محلولة"
    assert T.status_label("UNSOLVED", "ar") == "غير محلولة"


def test_channels_are_central():
    assert [T.channel(l)["name"] for l in ("en", "de", "fa", "ar")] == [
        "ClueVera", "Fallspur", "رد خاموش", "أثر خفي"]


@pytest.mark.parametrize("lang,title,expected", [
    ("en", "The Vanishing Circle", "The Vanishing Circle (Unsolved) | ClueVera"),
    ("de", "Der verschwundene Kreis", "Der verschwundene Kreis (Ungelöst) | Fallspur"),
    ("fa", "دایره‌ی ناپدید", "دایره‌ی ناپدید (حل‌نشده) | رد خاموش"),
    ("ar", "الدائرة المختفية", "الدائرة المختفية (غير محلولة) | أثر خفي"),
])
def test_youtube_title_format(lang, title, expected):
    assert T.build_youtube_title(title, "UNSOLVED", lang) == expected


def test_no_episode_number_by_default():
    t = T.build_youtube_title("Episode 273 The Vanishing Circle", "SOLVED", "en",
                              episode_sequence=273)
    assert "273" not in t and t == "The Vanishing Circle (Solved) | ClueVera"
    assert "Folge" not in T.build_youtube_title("Folge 12: Der Kreis", "SOLVED", "de")
    assert "#" not in T.build_youtube_title("Der Kreis #12", "SOLVED", "de")


def test_long_title_is_shortened_but_status_and_channel_stay():
    t = T.build_youtube_title("A very long editorial title " * 8, "UNSOLVED", "en")
    assert len(t) <= ai_config.video_identity.max_title_chars
    assert "…" in t
    assert t.endswith("(Unsolved) | ClueVera")


def test_case_uid_and_provenance(db_session):
    case = _case(db_session, "Identity uid case")
    assert case.case_uid.startswith("CASE_") and len(case.case_uid) == 11
    other = _case(db_session, "Identity uid other")
    assert other.case_uid != case.case_uid
    prov = T.resolution_provenance(db_session, case)
    assert prov["case_resolution_status"] == "UNSOLVED"
    assert prov["resolution_confidence"] == 0.97
    assert [s["id"] for s in prov["resolution_sources"]] == ["SRC_12", "SRC_44"]
    assert prov["needs_review"] is False
    review = _case(db_session, "Identity needs review", status="UNKNOWN")
    assert T.resolution_provenance(db_session, review)["needs_review"] is True


def test_backfill_assigns_missing_uids(db_session):
    case = _case(db_session, "Identity backfill")
    case.case_uid = None
    db_session.commit()
    assert T.backfill_case_uids(db_session) >= 1
    db_session.refresh(case)
    assert case.case_uid


def test_identity_keeps_sequence_internal_and_locks_after_publish(db_session):
    case = _case(db_session, "Identity lock")
    ident = T.sync_identity(db_session, case, "de", title="Der verschwundene Kreis",
                            episode_sequence=273)
    assert ident.episode_sequence == 273
    assert ident.youtube_title == "Der verschwundene Kreis (Ungelöst) | Fallspur"
    assert "273" not in ident.youtube_title
    assert ident.channel_id == "de" and ident.case_uid == case.case_uid
    ident.editorial_title = "Der leere Kreis"      # free until published
    T.sync_identity(db_session, case, "de", title="Der leere Kreis")
    T.mark_published(db_session, ident)
    with pytest.raises(T.TitleLocked):
        T.sync_identity(db_session, case, "de", title="Ein anderer Titel")
    # status changes do not rewrite a published identity
    set_resolution(db_session, case, "SOLVED", changed_by="monitor", reason="verdict")
    T.sync_identity(db_session, case, "de")
    assert ident.youtube_title.endswith("(Ungelöst) | Fallspur")
    T.sync_identity(db_session, case, "de", title="Ein anderer Titel", revise=True,
                    status="SOLVED")
    assert ident.title_version == 2 and "(Gelöst)" in ident.youtube_title
    assert ident.published_title == "Der leere Kreis"


def test_identity_endpoint(client, db_session):
    case = _case(db_session, "Identity endpoint")
    body = client.get(f"/api/cases/{case.id}/identity").json()
    assert body["case_uid"] == case.case_uid
    assert body["provenance"]["public_status"] == "unsolved"
    assert body["languages"]["fa"]["resolution_label"] == "حل‌نشده"
    assert body["languages"]["ar"]["channel"] == "أثر خفي"

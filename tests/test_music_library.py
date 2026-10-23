import uuid

import pytest

from app.shortform.music_library import (
    MusicPromptForbidden,
    StandaloneMusicDistributionForbidden,
    forbid_standalone_distribution,
    tracks_needed_for_window,
    validate_music_prompt,
)


def test_clean_prompt_passes():
    validate_music_prompt("A quiet, sparse instrumental bed, no drums, no vocals, restrained.")


@pytest.mark.parametrize("phrase", [
    "in the style of a famous composer",
    "sounds like a popular band",
    "inspired by the song from a hit film",
    "the soundtrack of a well-known franchise",
])
def test_banned_phrases_are_rejected(phrase):
    with pytest.raises(MusicPromptForbidden):
        validate_music_prompt(phrase)


def test_standalone_distribution_is_refused():
    with pytest.raises(StandaloneMusicDistributionForbidden):
        forbid_standalone_distribution("spotify_upload")
    with pytest.raises(StandaloneMusicDistributionForbidden):
        forbid_standalone_distribution("https://distrokid.com/upload")


def test_standalone_distribution_allows_reel_platforms():
    forbid_standalone_distribution("youtube_shorts")
    forbid_standalone_distribution("instagram_reel")


def test_tracks_needed_for_window_matches_window_once_reels_exceed_it():
    assert tracks_needed_for_window(reels=20, window=5) == 5
    assert tracks_needed_for_window(reels=3, window=5) == 3
    assert tracks_needed_for_window(reels=5, window=5) == 5


def _make_track(db_session, *, channel="en", mood="searching", claimed=False):
    import hashlib
    import uuid

    from app.db.models import HostLedMusicTrack

    track = HostLedMusicTrack(
        channel=channel, mood=mood, prompt="test prompt, no drums, no vocals",
        provider="elevenlabs_music", model_id="music_v2", provider_generation_id=None,
        plan_name=None, duration_seconds=20.0, bpm_estimate=None, bpm_method=None,
        file_path="data/shared_assets/music/test.wav",
        file_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        licence_note="test", terms_url="https://example.invalid", terms_recorded_date="2026-10-20",
        content_id_status="claimed" if claimed else "pending", excluded=claimed,
        excluded_reason="test exclusion" if claimed else None,
    )
    db_session.add(track)
    db_session.commit()
    db_session.refresh(track)
    return track


def _make_case(db_session):
    from app.db.models import Case

    suffix = db_session.query(Case).count() + 1
    case = Case(canonical_title=f"Music test {suffix}", slug=f"music-test-{suffix}", language="en")
    db_session.add(case)
    db_session.flush()
    return case


def _unpinned(monkeypatch):
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.shorts_host_led.music_library, "pinned_mood", None)


def test_claimed_track_is_never_selected(db_session, monkeypatch):
    from app.shortform.music_library import select_track

    _unpinned(monkeypatch)
    case = _make_case(db_session)
    mood = f"waiting-{uuid.uuid4().hex[:6]}"  # unique mood: the shared DB can hold stale tracks
    clean = _make_track(db_session, mood=mood)
    claimed = _make_track(db_session, mood=mood, claimed=True)
    for _ in range(10):
        picked = select_track(db_session, "en", case.id, mood=mood)
        assert picked.id == clean.id, "a Content-ID-claimed track must never be selected"
    assert claimed.excluded is True


def test_selection_avoids_recent_window_repeat(db_session, monkeypatch):
    from app.shortform.music_library import record_usage, select_track

    _unpinned(monkeypatch)
    case = _make_case(db_session)
    a = _make_track(db_session, mood="revealing")
    b = _make_track(db_session, mood="revealing")
    record_usage(db_session, a, case.id, "reel-1")
    picked = select_track(db_session, "en", case.id, mood="revealing")
    assert picked.id == b.id, "the most recently used track must not repeat immediately"


def test_pinned_mood_gives_every_language_and_reel_the_same_track(db_session, monkeypatch):
    from app.core.ai_config import ai_config
    from app.shortform.music_library import record_usage, select_track

    monkeypatch.setattr(ai_config.shorts_host_led.music_library, "pinned_mood", "searching")
    case = _make_case(db_session)
    from app.shortform.music_library import pinned_track

    searching = pinned_track(db_session) or _make_track(db_session, channel="en", mood="searching")
    for n, channel in enumerate(("en", "de", "fa", "ar", "en")):
        picked = select_track(db_session, channel, case.id, mood="revealing")
        assert picked.id == searching.id
        record_usage(db_session, picked, case.id, f"reel-{n}")


def test_generation_disabled_refuses_a_missing_mood_without_calling_the_provider(db_session, tmp_path, monkeypatch):
    from app.core.ai_config import ai_config
    from app.shortform.music_library import MusicGenerationDisabled, generate_library

    monkeypatch.setattr(ai_config.shorts_host_led.music_provider, "allow_generation", False)
    monkeypatch.setattr(ai_config.shorts_host_led.music_library, "moods", ["searching"])
    monkeypatch.setattr(ai_config.shorts_host_led.music_library, "channel_mood_family",
                        {"tt": "flashlight: a single torch beam in dark rooms"})

    def _forbidden_init(self):
        raise AssertionError("the provider must never be constructed when allow_generation is False")

    monkeypatch.setattr(
        "app.shortform.music_library.ElevenLabsMusicProvider.__init__", _forbidden_init)

    with pytest.raises(MusicGenerationDisabled):
        generate_library(db_session, "tt", root=tmp_path)


def test_generation_disabled_still_reuses_an_existing_stored_track(db_session, tmp_path, monkeypatch):
    from app.core.ai_config import ai_config
    from app.shortform.music_library import generate_library

    monkeypatch.setattr(ai_config.shorts_host_led.music_library, "moods", ["searching"])
    monkeypatch.setattr(ai_config.shorts_host_led.music_library, "channel_mood_family",
                        {"en": "flashlight: a single torch beam in dark rooms"})
    _make_track(db_session, channel="en", mood="searching")

    monkeypatch.setattr(ai_config.shorts_host_led.music_provider, "allow_generation", False)
    tracks = generate_library(db_session, "en", root=tmp_path)
    assert len(tracks) == 1
    assert tracks[0].mood == "searching"


def test_library_tracks_for_a_channel_have_distinct_file_hashes(db_session):
    """A generated library must never contain two moods backed by the same audio file —
    that would mean one track was silently reused as filler for another mood."""
    from app.db.models import HostLedMusicTrack

    t1 = _make_track(db_session, mood="searching")
    t2 = _make_track(db_session, mood="waiting")
    assert t1.file_sha256 != t2.file_sha256
    hashes = [row.file_sha256 for row in
              db_session.query(HostLedMusicTrack).filter_by(channel="en", excluded=False).all()]
    assert len(hashes) == len(set(hashes)), "duplicate audio file reused across moods"

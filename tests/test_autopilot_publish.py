from datetime import UTC, datetime

from app.autopilot import publish as P
from app.core.ai_config import ai_config

NOW = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)


def package(platform, length_ok=True):
    return {"platform": platform, "language": "en", "files": {"video": "short.mp4"},
            "caption": "c", "cta": "x", "length_ok": length_ok,
            "disclosure": {"required": True, "label": "AI", "applies": True},
            "before_publishing": ["owner approval of the short"]}


def short(short_id, language="en", length_ok=True):
    return P.ShortInput(short_id=short_id, language=language,
                        packages={p: package(p, length_ok) for p in ai_config.autopilot_settings().platforms})


def film(language="en"):
    return P.FilmInput(language=language, video_id=7, file_path=f"data/{language}.mp4",
                       title="First suggestion", description="d", tags=["t"])


def test_a_film_request_is_a_youtube_upload_with_an_unconfirmed_title_and_no_send():
    requests, skipped = P.plan_requests([film()], [], {("en", "youtube_short"): "@a"}, NOW)
    assert skipped == [] and len(requests) == 1
    r = requests[0]
    assert (r.kind, r.platform, r.language, r.short_id) == ("film", "youtube", "en", None)
    assert r.payload["title"] == "First suggestion" and r.payload["title_confirmed"] is False
    assert r.payload["sent"] is False and r.payload["dry_run"] is True


def test_every_short_gets_one_request_per_platform_with_the_channel_handle():
    handles = {("en", "tiktok_video"): "@tt"}
    requests, _ = P.plan_requests([], [short(1)], handles, NOW)
    platforms = ai_config.autopilot_settings().platforms
    assert [r.platform for r in requests] == platforms
    tiktok = next(r for r in requests if r.platform == "tiktok_video")
    assert tiktok.payload["channel_handle"] == "@tt" and tiktok.payload["sent"] is False
    assert next(r for r in requests if r.platform == "youtube_short").payload["channel_handle"] == ""


def test_a_short_that_does_not_fit_the_platform_length_is_skipped_not_queued():
    requests, skipped = P.plan_requests([], [short(1, length_ok=False)], {}, NOW)
    assert requests == []
    assert {s["platform"] for s in skipped} == set(ai_config.autopilot_settings().platforms)
    assert all("length" in s["reason"] for s in skipped)


def test_each_channel_posts_its_shorts_one_slot_apart_after_the_film():
    cfg = ai_config.autopilot_settings().schedule
    requests, _ = P.plan_requests([film()], [short(1), short(2)], {}, NOW)
    film_at = next(r for r in requests if r.kind == "film").scheduled_for
    assert film_at == datetime(2026, 10, 21, cfg.first_slot_hour_utc, tzinfo=UTC)
    yt = sorted((r for r in requests if r.platform == "youtube_short"), key=lambda r: r.short_id)
    gap = (yt[1].scheduled_for - yt[0].scheduled_for).total_seconds() / 3600
    assert gap == cfg.slot_spacing_hours
    lead = (yt[0].scheduled_for - film_at).total_seconds() / 3600
    assert lead == cfg.film_before_shorts_hours


def test_the_first_slot_is_tomorrow_when_today_s_hour_has_passed():
    late = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    requests, _ = P.plan_requests([film()], [], {}, late)
    assert requests[0].scheduled_for.day == 22

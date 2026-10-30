"""Code-made graphics (docs/alternative_graphics.md): deterministic frames, cue accuracy, refusal of incomplete
data, right-to-left mirroring, stillness of text pages, golden thumbnails per language."""
import os
from pathlib import Path

import graphics_samples as S
import numpy as np
import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.documentary.graphics import shots as GS
from app.documentary.graphics.base import Graphic as GraphicBase
from app.documentary.graphics.base import GraphicUnavailable
from app.documentary.graphics.document import normalized, verify_verbatim
from app.documentary.graphics.headline import known_source
from app.documentary.graphics.page import Page, ground
from app.documentary.graphics.registry import GRAPHICS, graphic

LANGUAGES = ("en", "de", "fa", "ar")
GOLDEN = Path(__file__).parent / "golden" / "graphics"


def sample(kind: str, language: str) -> tuple[dict, float, list[float]]:
    """The design sample of a kind with the cues and the length its streaming schedule gives it."""
    data = {"conflict": S.conflict(language), "recap": S.recap(language),
            "timeline": S.timeline(language), "document": S.document(),
            "headline": S.headline(), "quote": {"text": S.TEXT[language]["fact"], "speaker": S.TEXT[language]["a"],
                                                "fact_id": "F1", "source": "BBC News",
                                                "url": "https://www.bbc.com/news/world-us-canada-1"},
            "fact_card": {"facts": [{"text": S.TEXT[language]["fact"], "fact_id": "F12"},
                                    {"text": S.TEXT[language]["ac"], "fact_id": "F13"}]}}[kind]
    schedule = GS.schedule_of(kind, data, language)
    return data, schedule.total + 2.0, list(schedule.cues)


KINDS = ("conflict", "recap", "timeline", "document", "headline", "quote", "fact_card")


def thumb(image: Image.Image) -> np.ndarray:
    return np.asarray(image.resize((240, 135), Image.LANCZOS), dtype=np.int16)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("kind", KINDS)
def test_golden_thumbnail_per_graphic_and_language(kind, language):
    data, duration, cues = sample(kind, language)
    image = graphic(kind).render(data, duration, cues, language).image_at(duration - 1.0)
    path = GOLDEN / f"{kind}_{language}.png"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        image.resize((240, 135), Image.LANCZOS).save(path)
    assert path.exists(), f"missing golden {path.name}; run with UPDATE_GOLDEN=1"
    gold = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    assert np.abs(thumb(image) - gold).mean() < 2.0


@pytest.mark.parametrize("kind", KINDS)
def test_same_data_cues_and_language_give_the_same_frames(kind):
    data, duration, cues = sample(kind, "en")
    a = graphic(kind).render(data, duration, cues, "en")
    b = graphic(kind).render(data, duration, cues, "en")
    for t in (0.5, cues[0] + 0.2, duration - 1):
        assert np.array_equal(a.frame_at(t), b.frame_at(t))


@pytest.mark.parametrize("kind", KINDS)
def test_a_page_shows_its_empty_frames_first_then_its_text_comes_in_and_it_ends_as_the_finished_page(kind):
    data, duration, cues = sample(kind, "en")
    r = graphic(kind).render(data, duration, cues, "en")
    opening = r.frame_at(0.0)
    assert np.array_equal(opening, r.frame_at(0.5))                        # nothing moves before the first word
    middle = r.frame_at(cues[-1] + 0.3)                                    # the last step has only just begun
    assert not np.array_equal(opening, middle)                             # words have come in
    end = r.frame_at(duration - 0.1)
    assert not np.array_equal(middle, end)
    assert np.array_equal(end, np.asarray(r.state(len(cues))))             # the finished page, pixel for pixel


@pytest.mark.parametrize("kind", KINDS)
def test_the_finished_page_stays_still_for_the_end_hold(kind):
    """Text pages never zoom, pan or drift: once the text has come in, the page is the same image until the end."""
    data, duration, cues = sample(kind, "en")
    r = graphic(kind).render(data, duration, cues, "en")
    hold = ai_config.graphics_settings().stream.end_hold_seconds
    assert np.array_equal(r.frame_at(duration - hold + 0.5), r.frame_at(duration - 0.1))


def test_the_timeline_shows_no_event_before_its_cue():
    data, duration, cues = sample("timeline", "en")
    r = graphic("timeline").render(data, duration, cues, "en")
    for k, cue in enumerate(cues):
        assert np.array_equal(r.frame_at(cue - 0.02), np.asarray(r.state(k)))      # the card before is complete


def test_persian_and_arabic_pages_are_mirrored():
    """The first side of a conflict sits on the left in English and on the right in Persian and Arabic."""
    def blue_x(language):
        data, duration, cues = sample("conflict", language)
        frame = graphic("conflict").render(data, duration, cues, language).frame_at(duration - 1)
        hexa = ai_config.graphics_settings().themes["conflict"].blue.lstrip("#")
        rgb = np.array([int(hexa[i:i + 2], 16) for i in (0, 2, 4)])
        mask = (np.abs(frame[250:520].astype(int) - rgb).sum(axis=2) < 12)        # the first side's rule
        return np.argwhere(mask.any(axis=0)).mean()
    assert blue_x("en") < 960 < blue_x("fa") and blue_x("en") < 960 < blue_x("ar")


def test_too_short_a_duration_or_too_few_cues_is_refused_not_stretched():
    data, _, cues = sample("conflict", "en")
    with pytest.raises(GraphicUnavailable, match="too short"):
        graphic("conflict").render(data, cues[-1] + 1.0, cues, "en")
    with pytest.raises(GraphicUnavailable, match="needs 2 cue"):
        graphic("conflict").render(data, 20.0, [1.0], "en")
    r = graphic("conflict").render(data, 60.0, cues[:2], "en")        # status cue left out: reported, not invented
    assert r.skipped == ["step 3"]
    with pytest.raises(GraphicUnavailable, match="rise"):
        graphic("conflict").render(data, 20.0, [4.0, 1.0, 9.0], "en")


@pytest.mark.parametrize("kind", KINDS)
def test_missing_data_is_refused_never_rendered_as_a_placeholder(kind):
    with pytest.raises(GraphicUnavailable):
        graphic(kind).render({}, 16.0, [2.0, 6.0, 10.0], "en")


def test_every_claim_needs_a_source_and_a_fact_id_and_the_status_must_be_known():
    data, duration, cues = sample("conflict", "en")
    data["sides"][1]["fact_id"] = ""
    with pytest.raises(GraphicUnavailable, match="fact id"):
        graphic("conflict").render(data, duration, cues, "en")
    data, *_ = sample("conflict", "en")
    data["conflict"]["status"] = "won"
    with pytest.raises(GraphicUnavailable, match="status"):
        graphic("conflict").render(data, duration, cues, "en")


def test_a_text_too_long_for_its_box_makes_the_graphic_unavailable():
    data, duration, cues = sample("conflict", "en")
    data["sides"][0]["claim"] = "W" * 150                     # within the character limit, wider than the card
    with pytest.raises(GraphicUnavailable, match="needs more than"):
        graphic("conflict").render(data, duration, cues, "en")


def test_a_document_quote_must_have_been_checked_against_the_stored_text():
    text = "The officers   found the garage\ndoor open and a single set of footprints."
    assert verify_verbatim(text, "found the garage door open")
    assert not verify_verbatim(text, "found the garage door wide open")
    assert normalized(" a \n b ") == "a b"
    data, duration, cues = sample("document", "en")
    data["quote"]["verified"] = False
    with pytest.raises(GraphicUnavailable, match="checked"):
        graphic("document").render(data, duration, cues, "en")


def test_only_listed_newspapers_make_a_headline_card():
    assert known_source("https://www.nytimes.com/2022/x.html") == "The New York Times"
    assert known_source("https://news.bbc.co.uk/a") == "BBC News"
    assert known_source("https://nytimes.com.evil.example/x") is None
    data, duration, cues = sample("headline", "en")
    data["url"] = "https://blog.example.com/post"
    with pytest.raises(GraphicUnavailable, match="listed newspaper"):
        graphic("headline").render(data, duration, cues, "en")
    data, *_ = sample("headline", "en")
    data["published"] = {"year": 2022, "month": 11}
    with pytest.raises(GraphicUnavailable, match="full publication date"):
        graphic("headline").render(data, duration, cues, "en")


def test_every_graphic_type_has_labels_in_the_four_languages_and_is_registered():
    assert set(GRAPHICS) >= set(KINDS)
    labels = ai_config.graphics_settings().labels
    assert all(set(labels[lang]) == set(labels["en"]) for lang in LANGUAGES)
    assert labels["de"]["detective_board"] == "DETEKTIVTAFEL" and labels["en"]["detective_board"] == "DETECTIVE BOARD"


def test_the_conflict_label_is_amber_as_in_the_design():
    data, duration, cues = sample("conflict", "en")
    frame = graphic("conflict").render(data, duration, cues, "en").frame_at(duration - 1)
    label = frame[85:125, 120:420].reshape(-1, 3)
    amber = np.array([int(ai_config.graphics_settings().themes["conflict"].amber.lstrip("#")[i:i + 2], 16)
                      for i in (0, 2, 4)])
    assert (np.abs(label.astype(int) - amber).sum(axis=1) < 60).any()


class LowPage(GraphicBase):
    kind = "recap"
    """A graphic whose content reaches down to `bottom` (a test double for the subtitle-zone rule)."""

    kind = "recap"

    def __init__(self, bottom: int):
        self.bottom = bottom

    def can_render(self, data):
        return None

    def required_cues(self, data):
        return 1

    def draw_state(self, data, language, step):
        page = Page(language)
        page.rect(120, 200, 1200, self.bottom - 200, fill=(200, 160, 40))
        return page.done()


def test_content_is_shrunk_to_end_above_the_subtitle_zone_and_all_pages_alike():
    zone = ai_config.graphics_settings().safe_zone
    limit = 1080 - int(zone["bottom"])
    shown = LowPage(1000).render({}, 8.0, [1.0], "en")
    assert 0.8 < shown.scale() < 1.0
    for step in (0, 1):
        page = np.asarray(shown.state(step).convert("RGB"), dtype=np.int16)
        assert page.shape == (1080, 1920, 3)
        assert not (np.abs(page[limit + 2:] - np.asarray(ground(1920, 1080, "recap").convert("RGB"), dtype=np.int16)[limit + 2:])
                    .max(axis=2) > zone["tolerance"]).any()
    assert LowPage(limit - 10).render({}, 8.0, [1.0], "en").scale() == 1.0


def test_a_page_that_would_have_to_shrink_too_far_is_refused():
    with pytest.raises(GraphicUnavailable, match="subtitle zone"):
        LowPage(1075).render({}, 8.0, [1.0], "en")


def _points(count: int) -> dict:
    return {"points": [{"date": {"year": 2026, "month": 1 + n // 3, "day": 10 + n}, "labels": [f"Event {n}"],
                        "fact_id": f"F{n}"} for n in range(count)]}


def test_a_timeline_runs_later_with_every_point_and_events_of_one_date_share_a_point():
    from app.documentary.graphics import timeline as TL

    card = TL.Timeline()
    assert card.can_render(_points(8)) is None and "at most 8" in card.can_render(_points(9))
    data = _points(3)
    data["points"][2]["date"] = data["points"][1]["date"]
    assert "later than the one before" in card.can_render(data), "two points of one date must be one point"
    data = _points(3)
    data["points"][1]["date"] = {"year": 2025, "month": 1, "day": 1}
    assert "later than the one before" in card.can_render(data)
    data = _points(2)
    data["points"][0]["labels"] = ["a", "b", "c", "d"]
    assert card.can_render(data) is not None


def test_the_timeline_turns_at_the_page_edge_like_a_snake_and_every_point_is_on_the_page():
    from app.documentary.graphics import timeline as TL

    nodes = TL.node_positions(8)
    assert TL.rows_of(4) == 1 and TL.rows_of(5) == 2
    assert [y for _, y in nodes[:4]] == [TL.ROW_Y[2][0]] * 4 and [y for _, y in nodes[4:]] == [TL.ROW_Y[2][1]] * 4
    assert [x for x, _ in nodes[:4]] == sorted(x for x, _ in nodes[:4]), "the first row runs to the right"
    assert [x for x, _ in nodes[4:]] == sorted((x for x, _ in nodes[4:]), reverse=True), "the second row runs back"
    pts, at = TL.path_points(8)
    assert at == sorted(at) and all(0 <= x <= 1920 and 0 <= y <= 1080 for x, y in pts), "the whole snake is on the page"
    assert max(x for x, _ in pts) > TL.ROW_X1, "the line turns beyond the last point of a row"


def test_the_snake_starts_at_the_right_in_persian_and_arabic_and_grows_with_each_point():
    import numpy as np

    from app.documentary.graphics import timeline as TL

    card = TL.Timeline()
    data = _points(8)
    en, fa = card.draw_state(data, "en", 6), card.draw_state(data, "fa", 6)
    assert en.size == (1920, 1080)
    a, b = np.asarray(card.draw_state(data, "en", 2).convert("RGB")), np.asarray(en.convert("RGB"))
    assert not np.array_equal(a, b), "a new point appears with each reveal"
    first_en = np.argwhere(np.asarray(card.draw_state(data, "en", 1).convert("RGB")).sum(axis=2) > 0)
    assert first_en.size and card.draw_state(data, "fa", 1).size == (1920, 1080) and fa.size == (1920, 1080)


def test_quote_card_accepts_only_a_listed_newspaper_or_tv_broadcaster():
    card = GRAPHICS["quote"]
    base = {"text": "A quotation.", "fact_id": "F1", "source": "BBC News", "url": "https://www.bbc.com/news/x"}
    assert card.can_render(base) is None
    assert card.can_render({**base, "source": "ZDF", "url": "https://www.zdf.de/nachrichten/x"}) is None
    assert "not a listed" in card.can_render({**base, "url": "https://someblog.example.com/x"})
    assert "not a listed" in card.can_render({k: v for k, v in base.items() if k != "url"})
    assert "does not match" in card.can_render({**base, "source": "CNN"})


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_card_kind_has_its_own_readable_theme_and_map_and_board_have_none():
    themes = ai_config.graphics_settings().themes
    assert {"map", "detective_board"}.isdisjoint(themes)
    assert len({(t.frame, t.header) for t in themes.values()}) == len(themes), "every card has its own layout"
    assert len({t.amber for t in themes.values()}) == len(themes), "every card has its own accent colour"
    for kind, t in themes.items():
        for accent in (t.amber, t.blue, t.red):
            assert _contrast(accent, t.card) >= 4.5, f"{kind}: accent {accent} is hard to read"

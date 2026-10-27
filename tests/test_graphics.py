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
from app.documentary.graphics.keyfacts import extract
from app.documentary.graphics.page import Page, ground
from app.documentary.graphics.registry import GRAPHICS, graphic

LANGUAGES = ("en", "de", "fa", "ar")
GOLDEN = Path(__file__).parent / "golden" / "graphics"


def sample(kind: str, language: str) -> tuple[dict, float, list[float]]:
    """The design sample of a kind with the cues and the length its streaming schedule gives it."""
    data = {"conflict": S.conflict(language), "conflict_board": S.board(language), "recap": S.recap(language),
            "timeline": S.timeline(language), "key_facts": S.key_facts(language), "document": S.document(),
            "headline": S.headline(), "floor_plan": S.floor_plan(), "quote": {"text": S.TEXT[language]["fact"], "speaker": S.TEXT[language]["a"],
                                                "fact_id": "F1"}}[kind]
    schedule = GS.schedule_of(kind, data, language)
    return data, schedule.total + 2.0, list(schedule.cues)


KINDS = ("conflict", "conflict_board", "recap", "timeline", "key_facts", "document", "headline", "quote", "floor_plan")


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
        hexa = ai_config.graphics_settings().colors["blue"].lstrip("#")
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


def test_key_figures_come_from_the_fact_and_a_fact_without_a_figure_gives_none():
    assert extract("The police arrived at 11:58 p.m. on the night of the fire.")["value"] == "11:58 p.m."
    assert extract("He was sentenced to 25 years in prison.")["value"] == "25"
    assert extract("No one answered.") is None
    assert extract("x " * 80 + "7") is None


def test_the_floor_plan_is_never_invented():
    with pytest.raises(GraphicUnavailable, match="no real plan"):
        graphic("floor_plan").render({"rooms": [{"id": "a", "label": "A", "x": 0, "y": 0, "w": 1, "h": 1}],
                                      "markers": [{"kind": "found", "room": "a", "text": "t", "fact_id": "F"}]},
                                     16.0, [2.0], "en")
    plan = S.floor_plan()
    plan["doors"] = [{"x": 2, "y": 5, "axis": "x"}]
    with pytest.raises(GraphicUnavailable, match="axis"):
        graphic("floor_plan").render(plan, 16.0, [2.0, 5.0, 8.0], "en")
    assert graphic("floor_plan").render(S.floor_plan(), 16.0, [2.0, 5.0, 8.0], "en").image_at(14.0).size == (1920, 1080)


def test_the_board_lists_one_to_twelve_conflicts_and_names_the_new_one():
    data, duration, cues = sample("conflict_board", "en")
    data["new_card"] = "C9"
    with pytest.raises(GraphicUnavailable, match="new_card"):
        graphic("conflict_board").render(data, duration, cues, "en")
    many = {"cards": [{"id": f"C{n}", "number": n, "title": "t", "a": "a", "b": "b", "status": "open"}
                      for n in range(1, 13)], "new_card": "C12"}
    assert graphic("conflict_board").render(many, 14.0, [2.0], "en").image_at(8.0).size == (1920, 1080)


def test_every_graphic_type_has_labels_in_the_four_languages_and_is_registered():
    assert set(GRAPHICS) >= set(KINDS) | {"floor_plan"}
    labels = ai_config.graphics_settings().labels
    assert all(set(labels[lang]) == set(labels["en"]) for lang in LANGUAGES)
    assert labels["de"]["detective_board"] == "DETEKTIVTAFEL" and labels["en"]["detective_board"] == "DETECTIVE BOARD"


def test_the_conflict_board_shows_only_what_has_been_told_never_what_is_still_to_come():
    data, duration, cues = sample("conflict_board", "en")
    plain = graphic("conflict_board").render(data, duration, cues, "en")
    with_total = graphic("conflict_board").render({**data, "total": 6}, duration, cues, "en")
    assert np.array_equal(plain.frame_at(1.0), with_total.frame_at(1.0))        # a total in the data changes nothing
    assert np.array_equal(plain.frame_at(duration - 1), with_total.frame_at(duration - 1))
    before, after = plain.frame_at(1.0), plain.frame_at(duration - 1)
    assert before.shape == after.shape and not np.array_equal(before, after)       # the new card appears on its cue
    assert not ai_config.graphics_settings().labels["en"].get("not_yet_told")


def test_the_conflict_label_is_amber_as_in_the_design():
    data, duration, cues = sample("conflict", "en")
    frame = graphic("conflict").render(data, duration, cues, "en").frame_at(duration - 1)
    label = frame[85:125, 120:420].reshape(-1, 3)
    amber = np.array([int(ai_config.graphics_settings().colors["amber"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)])
    assert (np.abs(label.astype(int) - amber).sum(axis=1) < 60).any()


class LowPage(GraphicBase):
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
        assert not (np.abs(page[limit + 2:] - np.asarray(ground(1920, 1080).convert("RGB"), dtype=np.int16)[limit + 2:])
                    .max(axis=2) > zone["tolerance"]).any()
    assert LowPage(limit - 10).render({}, 8.0, [1.0], "en").scale() == 1.0


def test_a_page_that_would_have_to_shrink_too_far_is_refused():
    with pytest.raises(GraphicUnavailable, match="subtitle zone"):
        LowPage(1075).render({}, 8.0, [1.0], "en")


def _road(count: int, shown: int = 0) -> dict:
    events = [{"date": {"year": 2026, "month": 2 + n // 3, "day": 10 + n}, "label": f"Event {n}", "fact_id": f"F{n}"}
              for n in range(count)]
    return {"events": events, "shown": shown}


def test_a_timeline_card_continues_the_road_of_the_card_before_it():
    from app.documentary.graphics import timeline as TL

    card = TL.Timeline()
    first, second = _road(5), _road(8, shown=5)
    assert card.can_render(second) is None and card.required_cues(second) == 3 and card.required_cues(first) == 5
    assert card.can_render(_road(5, shown=5)) is not None                         # nothing new to reveal
    before_end = card.draw_state(first, "en", 5)
    after_start = card.draw_state(second, "en", 0)
    assert list(before_end.getdata()) == list(after_start.getdata())            # the next card opens where this one ended


def test_every_point_of_the_road_stays_on_the_line_when_it_has_panned_away():
    import math

    from app.documentary.graphics import timeline as TL

    data = _road(9, shown=6)
    frame = TL.Timeline().draw_state(data, "en", 3).convert("RGB")
    offset, history = TL.layout(8)
    away = math.ceil(offset - TL.SLOT_FIRST / TL.SLOT_STEP)
    assert away >= 4
    ground = frame.getpixel((TL.LINE_LEFT + 4, TL.LINE_Y - 40))
    for j in range(away):
        x = round(TL.LINE_LEFT + TL.HISTORY_LEFT + history / away * j)
        assert frame.getpixel((x, TL.LINE_Y - 6)) != ground                       # a mark above the line at every point
        assert frame.getpixel((x, TL.LINE_Y - 6))[0] > 150                         # amber, not background

"""Code-made graphics (docs/alternative_graphics.md): deterministic frames, cue accuracy, refusal of incomplete
data, right-to-left mirroring, stillness of text pages, golden thumbnails per language."""
import os
from pathlib import Path

import graphics_samples as S
import numpy as np
import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.documentary.graphics.base import GraphicUnavailable
from app.documentary.graphics.document import normalized, verify_verbatim
from app.documentary.graphics.headline import known_source
from app.documentary.graphics.keyfacts import extract
from app.documentary.graphics.registry import GRAPHICS, graphic

LANGUAGES = ("en", "de", "fa", "ar")
GOLDEN = Path(__file__).parent / "golden" / "graphics"


def sample(kind: str, language: str) -> tuple[dict, float, list[float]]:
    data = {"conflict": S.conflict(language), "conflict_board": S.board(language), "recap": S.recap(language),
            "timeline": S.timeline(language), "key_facts": S.key_facts(language), "document": S.document(),
            "headline": S.headline(), "quote": {"text": S.TEXT[language]["fact"], "speaker": S.TEXT[language]["a"],
                                                "fact_id": "F1"},
            "text": {"text": S.TEXT[language]["ac"]}}[kind]
    cues = {"conflict": [2, 6, 10], "conflict_board": [2], "recap": [2, 6, 10], "timeline": [2, 5, 8],
            "key_facts": [2, 5], "document": [2], "headline": [2], "quote": [2], "text": [2]}[kind]
    return data, 16.0, [float(c) for c in cues]


KINDS = ("conflict", "conflict_board", "recap", "timeline", "key_facts", "document", "headline", "quote", "text")


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
def test_nothing_is_drawn_before_its_cue_and_a_cue_changes_the_page(kind):
    data, duration, cues = sample(kind, "en")
    r = graphic(kind).render(data, duration, cues, "en")
    before = r.frame_at(cues[0] - 0.01)
    assert np.array_equal(before, r.frame_at(0.0))          # the page before the first cue is the opening page
    assert not np.array_equal(before, r.frame_at(cues[0] + ai_config.graphics_settings().transition_seconds + 0.1))
    for earlier, later in zip(cues, cues[1:], strict=False):
        assert not np.array_equal(r.frame_at(earlier + 1.0), r.frame_at(later + 1.0))


@pytest.mark.parametrize("kind", KINDS)
def test_a_page_between_cues_does_not_move(kind):
    """Text pages never zoom, pan or drift: after its reveal a page is the same image until the next cue."""
    data, duration, cues = sample(kind, "en")
    r = graphic(kind).render(data, duration, cues, "en")
    settled = cues[0] + ai_config.graphics_settings().transition_seconds + 0.05
    end = (cues[1] if len(cues) > 1 else duration) - 0.05
    assert np.array_equal(r.frame_at(settled), r.frame_at((settled + end) / 2))
    assert np.array_equal(r.frame_at(settled), r.frame_at(end))


def test_the_timeline_shows_no_event_before_its_cue():
    data, duration, cues = sample("timeline", "en")
    r = graphic("timeline").render(data, duration, cues, "en")
    for k, cue in enumerate(cues):
        assert np.array_equal(r.frame_at(cue - 0.02), np.asarray(r.state(k)))


def test_persian_and_arabic_pages_are_mirrored():
    """The first side of a conflict sits on the left in English and on the right in Persian and Arabic."""
    def blue_x(language):
        data, duration, cues = sample("conflict", language)
        frame = graphic("conflict").render(data, duration, cues, language).frame_at(duration - 1)
        row = frame[425:432, :, :].astype(int)          # the cards' coloured top rules
        mask = (row[:, :, 2] > 150) & (row[:, :, 0] < 140)    # the blue rule
        return np.argwhere(mask.any(axis=0)).mean()
    assert blue_x("en") < 960 < blue_x("fa") and blue_x("en") < 960 < blue_x("ar")


def test_too_short_a_duration_or_too_few_cues_is_refused_not_stretched():
    data, _, cues = sample("conflict", "en")
    with pytest.raises(GraphicUnavailable, match="too short"):
        graphic("conflict").render(data, 11.0, cues, "en")
    with pytest.raises(GraphicUnavailable, match="needs 2 cue"):
        graphic("conflict").render(data, 20.0, [1.0], "en")
    r = graphic("conflict").render(data, 20.0, [1.0, 4.0], "en")      # status cue left out: reported, not invented
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
    plan = {"source": {"document_id": 4, "page": 2, "name": "Police report"},
            "rooms": [{"id": "a", "label": "Room A", "x": 0, "y": 0, "w": 2, "h": 2}],
            "markers": [{"kind": "found", "room": "a", "text": "Found here.", "fact_id": "F1"}]}
    assert graphic("floor_plan").render(plan, 12.0, [2.0], "en").image_at(8.0).size == (1920, 1080)


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

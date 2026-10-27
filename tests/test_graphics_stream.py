"""The text of every alternative is revealed word by word, in silence, slower than reading: first the empty page (its
frames, cards and lines), then the words in the order they are read, and at the end the finished page stays for the
viewer. A board that continues keeps what it shows and streams only the new text."""
import numpy as np
import pytest
from test_engines import SAMPLES as ENGINE_SAMPLES
from test_graphics import KINDS as PAGE_KINDS
from test_graphics import sample as page_sample

from app.core.ai_config import ai_config
from app.documentary.graphics import shots as GS
from app.documentary.graphics import streaming as ST
from app.documentary.graphics.page import WORD_PAD
from app.documentary.graphics.registry import graphic

ALL = tuple(PAGE_KINDS) + tuple(ENGINE_SAMPLES)
LANGUAGES = ("en", "fa")


def data_of(kind: str, language: str) -> dict:
    return page_sample(kind, language)[0] if kind in PAGE_KINDS else ENGINE_SAMPLES[kind]


def rendered(kind: str, language: str, slack: float = 2.0):
    data = data_of(kind, language)
    schedule = GS.schedule_of(kind, data, language)
    duration = schedule.total + slack
    return graphic(kind).render(data, duration, list(schedule.cues), language), schedule, duration


def test_the_seventeen_alternatives_are_all_covered():
    assert len(ALL) == 17 and set(ALL) == set(GS.T.config().hold_seconds) - {"conflict_board"} | {"conflict_board"}


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("kind", ALL)
def test_the_schedule_streams_at_a_pace_slower_than_reading_and_ends_with_a_hold(kind, language):
    cfg = ai_config.graphics_settings().stream
    schedule = GS.schedule_of(kind, data_of(kind, language), language)
    assert list(schedule.cues) == sorted(schedule.cues) and sum(schedule.counts) > 0
    streaming = sum(schedule.counts) / cfg.words_per_second
    assert schedule.total >= cfg.start_seconds + streaming + cfg.end_hold_seconds - 1e-6
    assert cfg.words_per_second <= 2.5                                         # a reading pace is 3 to 4 words a second
    assert GS.planned_seconds(kind, data_of(kind, language), language) >= schedule.total


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("kind", ALL)
def test_every_pixel_of_text_belongs_to_a_word_and_a_hidden_word_leaves_no_trace(kind, language):
    g, data = graphic(kind), data_of(kind, language)
    steps = g.required_cues(data)
    layer = ST.Layer(g, data, language, steps)
    assert layer.words
    text = np.abs(layer._full - layer._bare).max(axis=2) > ST.TEXT_DIFFERENCE
    assert not (text & (layer.owner < 0)).any()
    for i in {0, len(layer.words) // 2, len(layer.words) - 1}:
        visible = np.ones(len(layer.words), np.float32)
        visible[i] = 0.0
        shown = np.asarray(layer.compose(visible).convert("RGB"), dtype=np.float32)
        x0, y0, x1, y1 = layer.words[i].box
        core = (slice(max(y0, 0), y1), slice(max(x0 + WORD_PAD + 2, 0), max(x1 - WORD_PAD - 2, 1)))
        left = np.abs(shown[core] - layer._bare[core]).max(axis=2) > ST.TEXT_DIFFERENCE
        assert left.mean() < 0.03


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("kind", ALL)
def test_the_empty_page_comes_first_and_the_finished_page_last(kind, language):
    r, schedule, duration = rendered(kind, language)
    stream = r.stream()
    assert stream is not None
    first = r.frame_at(0.0)
    assert np.array_equal(first, r.frame_at(ai_config.graphics_settings().stream.start_seconds - 0.3))
    assert stream.words_shown(0.0) == 0.0
    assert np.array_equal(r.frame_at(duration - 0.05), np.asarray(r.state(len(schedule.cues))))
    assert not np.array_equal(first, r.frame_at(duration - 0.05))


@pytest.mark.parametrize("kind", ALL)
def test_words_come_in_one_by_one_never_faster_than_the_pace(kind):
    r, schedule, duration = rendered(kind, "en", slack=0.0)
    stream, cfg = r.stream(), ai_config.graphics_settings().stream
    counts = [stream.words_shown(t / 4) for t in range(int(duration * 4))]
    window = 4                                                                  # one second
    new_per_second = max(b - a for a, b in zip(counts, counts[window:], strict=False))
    if not stream.continued:
        assert all(b >= a - 1e-6 for a, b in zip(counts, counts[1:], strict=False))     # nothing disappears
    assert new_per_second <= cfg.words_per_second + 1.5                        # the fading word counts a little


@pytest.mark.parametrize("kind", ["conflict", "quote", "recap", "key_facts", "headline", "courtroom", "custody"])
def test_a_page_of_its_own_shows_all_its_frames_empty_before_the_first_word(kind):
    r, _, _ = rendered(kind, "en")
    stream = r.stream()
    assert not stream.continued
    empty = np.asarray(r.fitted(stream.layers[-1].bare))
    assert np.array_equal(np.asarray(r.frame_at(0.1)), empty)


@pytest.mark.parametrize("kind", ["timeline", "conflict_board", "document"])
def test_a_board_that_continues_keeps_what_it_shows_and_streams_only_the_new_text(kind):
    r, schedule, duration = rendered(kind, "en")
    stream = r.stream()
    assert stream.continued and len(stream.new) == len(schedule.cues) + 1
    for step in range(1, len(stream.new)):
        layer = stream.layers[step]
        known = set(range(len(layer.words))) - set(stream.new[step])
        # just before its words start, everything that was already on the board is drawn in full
        t = stream.cues[step - 1] + ST.lead_of(kind) + 0.01
        vis = stream._visible(layer, t, range(step, step + 1), known)
        assert all(vis[i] == 1.0 for i in known)
        assert all(vis[i] < 1.0 for i in stream.new[step])


def test_the_stream_is_silent_and_deterministic():
    r, schedule, duration = rendered("conflict", "en")
    again, _, _ = rendered("conflict", "en")
    for t in (3.0, 9.5, duration - 0.5):
        assert np.array_equal(r.frame_at(t), again.frame_at(t))


def test_a_shot_shorter_than_its_schedule_streams_faster_but_not_below_the_floor():
    data = data_of("conflict", "en")
    spec = {"type": "conflict", "data": data, "cue_fractions": []}
    schedule = GS.schedule_of("conflict", data, "en")
    cfg = ai_config.graphics_settings().stream
    ok = GS.cues_for(spec, schedule.total * (cfg.min_speed_scale + 0.02), "en")
    assert ok[-1] < schedule.cues[-1]
    with pytest.raises(GS.GraphicUnavailable, match="too short to stream"):
        GS.cues_for(spec, schedule.total * (cfg.min_speed_scale - 0.05), "en")
    assert GS.cues_for(spec, schedule.total + 30, "en") == list(schedule.cues)


def test_other_languages_get_their_own_schedule_from_their_own_words():
    data = data_of("conflict", "en")
    assert GS.schedule_of("conflict", data_of("conflict", "fa"), "fa").counts != GS.schedule_of("conflict", data, "en").counts

"""The drawing in the film: a corner window 60 % visible over pictures, the whole screen at 100 % over a gap, hidden over cards."""

from __future__ import annotations

import json
from uuid import uuid4

import cv2
import numpy as np
import pytest

from app.db.models import Case, EditorialBlueprint, StoryVersion, VisualAsset, VisualPlan
from app.documentary.drawing import portrait as PD
from app.documentary.drawing import script as DRS
from app.documentary.pipeline.errors import AwaitingPortrait
from app.documentary.render.drawing_layer import DrawingLayer

W, H = 640, 360


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    folder = tmp_path_factory.mktemp("portrait")
    img = np.full((900, 700, 3), 200, np.uint8)
    cv2.ellipse(img, (350, 400), (200, 280), 0, 0, 360, (90, 90, 90), -1)
    cv2.circle(img, (270, 340), 28, (20, 20, 20), -1)
    cv2.circle(img, (430, 340), 28, (20, 20, 20), -1)
    cv2.imwrite(str(folder / "face.jpg"), img)
    return PD.PortraitDrawing(folder / "face.jpg").render(folder / "out", seconds=20.0, fps=5)


def layer(made, mode="window", rtl=False, start=10.0, end=30.0, **seg):
    script = {"drawing": {"rtl": rtl, "portraits": {"P": {**made, "video": made["video"], "pen": made["pen"]}},
                          "segments": [{"start": start, "end": end, "mode": mode, "portrait": "P", "from": 0.0, "speed": 1.0, **seg}]}}
    return DrawingLayer(script, W, H)


def black():
    return np.zeros((H, W, 3), np.uint8)


def test_over_a_card_there_is_no_segment_and_the_frame_is_not_touched(made):
    frame = black()
    assert layer(made).apply(frame, 5.0) is frame


def test_the_window_is_flush_in_the_corner_120_by_150_of_1280_by_720_and_60_percent_visible(made):
    lay = layer(made)
    out = lay.apply(black(), 20.0)
    ww, wh = round(W * 120 / 1280), round(H * 150 / 720)
    assert (ww, wh) == (60, 75)
    assert (out[wh + 2:] == 0).all() and (out[:, ww + 2:] == 0).all(), "nothing outside the window"
    paper = lay._frame("P", int((20.0 - 10.0) * 5))
    expected = cv2.resize(paper, (ww, wh), interpolation=cv2.INTER_AREA).astype(np.float32) * 0.6
    inside = out[:wh, :ww].astype(np.float32)
    assert abs(inside.mean() - expected.mean()) < 12, "over a black picture the window is 60 % of the paper"
    assert out[0, 0].sum() > 0, "flush: the corner pixel is already the drawing"


def test_for_persian_and_arabic_the_window_is_in_the_top_right_corner(made):
    out = layer(made, rtl=True).apply(black(), 20.0)
    assert (out[:, :W - 62] == 0).all() and out[0, W - 1].sum() > 0


def test_over_a_gap_the_window_has_grown_to_the_whole_screen_at_100_percent_with_blurred_sides(made):
    lay = layer(made, mode="full")
    out = lay.apply(black(), 20.0)
    assert out.mean() > 100, "the whole screen is the drawing (100 %, not 60 %)"
    paper = lay._frame("P", int(10.0 * 5))
    portrait_w = round(H * paper.shape[1] / paper.shape[0])
    left = (W - portrait_w) // 2
    centre = out[H // 2, W // 2].astype(int)
    assert centre.sum() > 0 and out[H // 2, 5].sum() > 0, "the sides are filled, not black"
    assert portrait_w < W, "the portrait is never stretched to the screen's width"
    assert left > 10


def test_the_window_grows_over_the_configured_time_after_a_gap_begins_and_shrinks_before_it_ends(made):
    lay = layer(made, mode="full")
    start = lay.apply(black(), 10.0 + 0.05).mean()
    mid = lay.apply(black(), 10.0 + 0.4).mean()
    full = lay.apply(black(), 20.0).mean()
    end = lay.apply(black(), 30.0 - 0.05).mean()
    assert start < mid < full and end < full


def test_over_a_card_the_drawing_fades_in_and_out_instead_of_jumping(made):
    lay = layer(made, fade_in=True, fade_out=True)
    assert lay.apply(black(), 10.0).sum() == 0 and lay.apply(black(), 20.0).sum() > 0


def test_the_drawing_continues_from_its_own_progress_and_the_speed_the_planner_chose(made):
    lay = layer(made, speed=2.0)
    early = lay.apply(np.full((H, W, 3), 255, np.uint8), 10.0 + 0.5)
    late = lay.apply(np.full((H, W, 3), 255, np.uint8), 29.0)
    assert late[:75, :60].mean() < early[:75, :60].mean(), "more graphite later in the drawing"


def _film(db, language="en"):
    case = Case(canonical_title="Layer", slug=f"dl-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    needs = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json="{}",
                       requirements_json=json.dumps({"entities": [{"key": "ashley_flynn", "name": "Ashley", "type": "person"}]}))
    db.add(needs)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=2, validation_json=json.dumps({"mvp": {"needs_plan_id": needs.id}}))
    db.add(plan)
    db.add(VisualAsset(case_id=case.id, asset_code="PORT", asset_type="photo", local_path="p.jpg", draw_priority=1,
                       entity_key="ashley_flynn", entity_type="person", spec_json="{}"))
    db.commit()
    return case, plan


SCRIPT = {"subtitles": [{"start": 0.0, "end": 5.0, "text": "Ashley taught children."}],
          "overlays": [{"kind": "label", "text": "illustration", "start": 10.0, "end": 20.0}],
          "shots": [{"kind": "image", "start": 0.0, "end": 40.0}, {"kind": "graphic", "start": 40.0, "end": 60.0},
                    {"kind": "image", "start": 60.0, "end": 400.0}]}


def test_a_film_whose_portraits_are_not_made_is_not_made_and_the_run_says_to_create_them(db_session):
    case, plan = _film(db_session)
    with pytest.raises(AwaitingPortrait, match="Awaiting portrait creation: 1 of the 1 portraits"):
        DRS.build(db_session, case, plan, json.loads(json.dumps(SCRIPT)), "en")


def test_a_made_portrait_gives_the_segments_in_the_languages_corner_and_moves_the_label_beside_the_window(db_session, made):
    case, plan = _film(db_session)
    asset = db_session.query(VisualAsset).filter_by(case_id=case.id, asset_code="PORT").one()
    asset.spec_json = json.dumps({"drawing": {"status": "ready", "video": made["video"], "pen": made["pen"], "fps": made["fps"],
                                              "frames": made["frames"], "updated": 0}})
    db_session.commit()
    script = json.loads(json.dumps(SCRIPT))
    out = DRS.build(db_session, case, plan, script, "fa")
    assert out["rtl"] is True and out["portraits"]["PORT"]["frames"] == made["frames"]
    assert all(s["portrait"] == "PORT" for s in out["segments"]) and out["segments"][0]["fade_in"] is True
    assert not any(40.0 < (s["start"] + s["end"]) / 2 < 60.0 for s in out["segments"]), "hidden over the card"
    assert script["overlays"][0]["offset_fraction"] == 0.0938, "the label moves beside the window"


def test_the_illustration_label_is_drawn_beside_the_window_in_both_directions():
    from app.documentary.render.engine import Overlays

    def box(language, offset):
        script = {"language": language, "overlays": [{"kind": "label", "text": "illustration", "start": 0.0, "end": 5.0,
                                                      **({"offset_fraction": offset} if offset else {})}]}
        return Overlays(script, 1280, 720, False).items[0][2]

    assert box("en", 0.0938)[0] - box("en", 0)[0] == round(1280 * 0.0938), "to the right of the window in English"
    assert box("fa", 0)[0] - box("fa", 0.0938)[0] == round(1280 * 0.0938), "to the left of the window in Persian"

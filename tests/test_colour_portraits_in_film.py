"""Every new case draws its portraits in colour, and the colour reaches the film's frame through the drawing layer."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from test_portrait_drawing import _saturation, colour_photo_file

from app.core.ai_config import ai_config
from app.documentary.drawing import portrait as PD
from app.documentary.drawing import service as S
from app.documentary.drawing.portrait_colour import ColourPortraitDrawing
from app.documentary.render.drawing_layer import DrawingLayer

W, H = 640, 360


def _saturation_in_the_window(made, at=19.5):
    script = {"drawing": {"rtl": False, "portraits": {"P": made},
                          "segments": [{"start": 0.0, "end": 20.0, "mode": "window", "portrait": "P", "from": 0.0, "speed": 1.0}]}}
    frame = DrawingLayer(script, W, H).apply(np.zeros((H, W, 3), np.uint8), at)
    return _saturation(cv2.cvtColor(frame[:75, :60], cv2.COLOR_RGB2BGR))      # the film's frames are RGB


class _Session:
    """The test's session as the worker's own: `with SessionLocal() as db`, closed by nobody."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *_):
        return False


def test_the_app_makes_the_colour_portrait_only(db_session, tmp_path, monkeypatch):
    from app.db.models import Case, VisualAsset

    assert not hasattr(ai_config.drawing_settings(), "style") and not hasattr(S, "drawing_class"), "there is no way to ask for the grey drawing"
    case = Case(canonical_title="Portrait", slug="portrait-colour-only", language="en")
    db_session.add(case)
    db_session.commit()
    asset = VisualAsset(case_id=case.id, asset_code="PORT", asset_type="photo", local_path=str(colour_photo_file(tmp_path)), spec_json="{}")
    db_session.add(asset)
    db_session.commit()
    drawn_with = []
    monkeypatch.setattr(S, "SessionLocal", lambda: _Session(db_session))
    monkeypatch.setattr(S, "folder", lambda case_id, code: tmp_path)
    monkeypatch.setattr(ColourPortraitDrawing, "render", lambda self, *a, **k: drawn_with.append(type(self).__name__) or (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(PD.PortraitDrawing, "render", lambda self, *a, **k: drawn_with.append("grey") or (_ for _ in ()).throw(RuntimeError("stop")))
    S.make(asset.id)
    assert drawn_with == ["ColourPortraitDrawing"]


@pytest.fixture(autouse=True)
def drawing_as_long_as_the_test_portrait(monkeypatch):
    """The test portrait is 20 s long: a portrait takes that long at normal speed here."""
    monkeypatch.setattr(ai_config.drawing_settings(), "base_seconds", 20.0)


@pytest.fixture(scope="module")
def portraits(tmp_path_factory):
    folder = tmp_path_factory.mktemp("colour")
    photo = colour_photo_file(folder)
    return {"colour": ColourPortraitDrawing(photo).render(folder / "colour", seconds=20.0, fps=5),
            "grey": PD.PortraitDrawing(photo).render(folder / "grey", seconds=20.0, fps=5)}


def test_the_colour_of_a_colour_portrait_reaches_the_frame_of_the_film_and_a_grey_one_stays_grey(portraits):
    colour, grey = _saturation_in_the_window(portraits["colour"]), _saturation_in_the_window(portraits["grey"])
    assert colour > grey + 8, (colour, grey)

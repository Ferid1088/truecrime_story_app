"""The Alternative Visual Design Director (Section G): real render -> real vision review loop for
one dispatched detective visual.

    img, measured = render_preview("fact_card", data, "en")
    raw = await review(db, tool_kind="fact_card", data=data, language="en",
                       detective_intent=intent, scene_id="s1", visual_id="v1")

The application runs design -> real render -> review -> validated changes -> re-render -> review
(doc's own loop). This module provides the render+review step; applying `changes` back onto a
tool's `data` dict and re-rendering is the caller's job (same split as director.py's map/direct
functions, which don't orchestrate repair cycles themselves either).
"""

from __future__ import annotations

import json

from PIL import Image
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.documentary.detective.design_schema import DesignOutputError, validate_design_output
from app.documentary.graphics.base import lowest_content_row
from app.documentary.graphics.registry import graphic
from app.documentary.visuals import images as IM
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

OUTPUT_PROFILE = {"width": 1920, "height": 1080, "aspect_ratio": "16:9",
                  "smaller_viewing_size": {"width": 480, "height": 270}}


class DesignValidationFailed(ValueError):
    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


def render_preview(tool_kind: str, data: dict, language: str, step: int | None = None) -> tuple[Image.Image, dict]:
    """The REAL renderer (same `draw_state` the golden tests compare), plus one deterministic,
    content-independent measurement: how close the drawn content comes to the bottom safe edge."""
    g = graphic(tool_kind)
    reason = g.can_render(data)
    if reason:
        raise DesignValidationFailed([f"tool data cannot render: {reason}"])
    cues = step if step is not None else g.required_cues(data)
    img = g.draw_state(data, language, cues)
    content_row = lowest_content_row(img, tolerance=6)
    fraction = (content_row / img.height) if content_row >= 0 else 0.0
    measured = {"width": img.width, "height": img.height,
               "content_bottom_fraction": round(fraction, 4),
               "near_bottom_edge": fraction > 0.90}
    return img, measured


def _preview_data_url(img: Image.Image) -> str:
    import io

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return IM.data_url(buf.getvalue())


def build_input(*, mode: str, run_id: str, film_id: str, scene_id: str, visual_id: str,
                language: str, text_version: int, detective_intent: dict,
                tool_kind: str, data: dict, measured: dict, preview_id: str,
                prior_review: dict | None = None) -> dict:
    payload = {
        "schema_version": "1.0", "mode": mode, "run_id": run_id, "film_id": film_id,
        "scene_id": scene_id, "visual_id": visual_id, "language": language,
        "text_version": text_version,
        "detective_intent": detective_intent,
        "visual_spec": {"tool_id": tool_kind, "data": data},
        "tool_schema": {"tool_id": tool_kind},
        "font_inventory": {"note": "repo's own theme fonts; no per-call substitution yet"},
        "output_profile": OUTPUT_PROFILE,
        "preview_manifest": [
            {"preview_id": f"{preview_id}-full", "state": "final, full frame"},
            {"preview_id": f"{preview_id}-small", "state": "final, resized to the declared smaller viewing size"},
        ],
        "deterministic_checks": measured,
    }
    if prior_review is not None:
        payload["prior_review"] = prior_review
    return payload


async def review(db: Session, case_id: int, *, tool_kind: str, data: dict, language: str,
                 detective_intent: dict, scene_id: str, visual_id: str,
                 run_id: str = "g-review", film_id: str = "g-film", text_version: int = 1,
                 mode: str = "review", prior_review: dict | None = None,
                 exact_texts: dict[str, str] | None = None) -> dict:
    """One real design/review call against the actual rendered frame."""
    img, measured = render_preview(tool_kind, data, language)
    preview_id = f"{tool_kind}-{scene_id}-{visual_id}"
    payload = build_input(
        mode=mode, run_id=run_id, film_id=film_id, scene_id=scene_id, visual_id=visual_id,
        language=language, text_version=text_version, detective_intent=detective_intent,
        tool_kind=tool_kind, data=data, measured=measured, preview_id=preview_id,
        prior_review=prior_review,
    )
    small = img.resize((OUTPUT_PROFILE["smaller_viewing_size"]["width"],
                        OUTPUT_PROFILE["smaller_viewing_size"]["height"]), Image.LANCZOS)
    images = [_preview_data_url(img), _preview_data_url(small)]
    gen = get_generation_provider()
    with track_run(db, case_id, "Visual Design Director: review",
                   input_summary=f"{tool_kind}:{scene_id}:{visual_id}") as run:
        raw, res = await run_agent("detective.design_director", gen,
                                   json.dumps(payload, ensure_ascii=False), images=images)
        stamp_run(run, res, "detective_design_director")
    violations = validate_design_output(raw, exact_texts=exact_texts or {})
    if violations:
        raise DesignValidationFailed(violations)
    return raw

"""Statement card: one confirmed fact of the case in large text, with its source. The look of the quote card ("on the
record") for what the record shows without a quotation. Its text streams in word by word like every other graphic."""

from __future__ import annotations

import json

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Page, mode_cache

TEXT_MAX_CHARS = 120


class FactCard(Graphic):
    kind = "fact_card"

    def can_render(self, data: dict) -> str | None:
        text = " ".join(str(data.get("text", "")).split())
        if not text or len(text) > TEXT_MAX_CHARS:
            return f"the fact must be 1-{TEXT_MAX_CHARS} characters"
        if not data.get("fact_id"):
            return "no fact id"
        return None

    def required_cues(self, data: dict) -> int:
        return 1

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _fact(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@mode_cache(maxsize=24)
def _fact(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    page = Page(language)
    m = page.margin
    page.tracked(m, 160, T.label(language, "fact_card"), "mono", T.size("label"), T.rgb("amber"), 5)
    if step >= 1:
        page.rect(m, 300, 8, 360, fill=T.rgb("amber"))
        end = page.text(m + 56, 300, page.W - 2 * m - 56, " ".join(data["text"].split()), "body", 88, T.rgb("ink"),
                        lines_max=4, min_px=52, spacing=1.22)
        if data.get("source"):
            source = f"{T.label(language, 'source')}: {data['source']}"
            page.text(m + 56, end + 40, page.W - 2 * m - 56, source if page.rtl else source.upper(), "mono", 28,
                      T.rgb("grey"), lines_max=2, min_px=20)
    return page.done()

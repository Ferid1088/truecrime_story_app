"""Quote card: a verified quotation with the newspaper or TV broadcaster it was published by. A still page: it fades in
and never moves."""

from __future__ import annotations

import json

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.document import QUOTE_MARKS
from app.documentary.graphics.outlets import outlet_of
from app.documentary.graphics.page import Page, mode_cache


class QuoteCard(Graphic):
    kind = "quote"

    def can_render(self, data: dict) -> str | None:
        text = " ".join(str(data.get("text", "")).split())
        if not text or len(text) > 220:
            return "the quote must be 1-220 characters"
        if not data.get("fact_id"):
            return "no fact id"
        if not data.get("url") or outlet_of(data["url"]) is None:
            return "the address is not a listed newspaper or TV broadcaster"
        if data.get("source") != outlet_of(data["url"]):
            return "the outlet name does not match its address"
        return None

    def required_cues(self, data: dict) -> int:
        return 1

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _quote(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@mode_cache(maxsize=24)
def _quote(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    page = Page(language)
    m = page.margin
    page.tracked(m, 160, T.label(language, "quote"), "mono", T.size("label"), T.rgb("amber"), 5)
    if step >= 1:
        opening, closing = QUOTE_MARKS[language]
        page.rect(m, 300, 8, 480, fill=T.rgb("red"))
        end = page.text(m + 56, 300, page.W - 2 * m - 56, f"{opening}{' '.join(data['text'].split())}{closing}",
                        "body", 76, T.rgb("ink"), lines_max=6, min_px=44, spacing=1.25)
        credit = ", ".join(filter(None, (data.get("speaker"), data["source"])))
        page.text(m + 56, end + 36, page.W - 2 * m - 56, credit, "mono", 32, T.rgb("grey"), lines_max=2, min_px=22)
    return page.done()

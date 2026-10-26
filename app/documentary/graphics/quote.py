"""Quote card (a verified quotation with who said it) and text card (one spoken sentence set large). Both are
still pages: they fade in and never move."""

from __future__ import annotations

import json
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.document import QUOTE_MARKS
from app.documentary.graphics.page import Page


class QuoteCard(Graphic):
    kind = "quote"

    def can_render(self, data: dict) -> str | None:
        text = " ".join(str(data.get("text", "")).split())
        if not text or len(text) > 220:
            return "the quote must be 1-220 characters"
        if not data.get("fact_id"):
            return "no fact id"
        return None

    def required_cues(self, data: dict) -> int:
        return 1

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _quote(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@lru_cache(maxsize=24)
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
        if data.get("speaker"):
            page.text(m + 56, end + 36, page.W - 2 * m - 56, data["speaker"], "mono", 32, T.rgb("grey"), lines_max=2,
                      min_px=22)
    return page.done()


class TextCard(Graphic):
    kind = "text"

    def can_render(self, data: dict) -> str | None:
        text = " ".join(str(data.get("text", "")).split())
        return None if 10 <= len(text) <= 200 else "the sentence must be 10-200 characters"

    def required_cues(self, data: dict) -> int:
        return 1

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _text(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@lru_cache(maxsize=24)
def _text(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    page = Page(language)
    if step >= 1:
        m = page.margin + 80
        page.rect(page.W // 2 - 45, 300, 90, 6, fill=T.rgb("red"))
        page.text(m, 350, page.W - 2 * m, " ".join(data["text"].split()), "body", 72, T.rgb("ink"), align="center",
                  lines_max=6, min_px=44, spacing=1.28)
    return page.done()

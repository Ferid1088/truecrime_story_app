"""Statement card: confirmed facts of the case in large text, one to three on one board, each a complete short sentence.
The look of the quote card ("on the record") for what the record shows without a quotation. No source is written on it.
Its text streams in word by word like every other graphic."""

from __future__ import annotations

import json

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Page, mode_cache

TEXT_MAX_CHARS = 120
MAX_FACTS = 3
LAYOUT = {1: (88, 4, 52), 2: (76, 3, 48), 3: (64, 2, 42)}       # facts on the board: text px, lines, smallest px


class FactCard(Graphic):
    kind = "fact_card"

    def can_render(self, data: dict) -> str | None:
        facts = data.get("facts")
        if not isinstance(facts, list) or not 1 <= len(facts) <= MAX_FACTS:
            return f"the card shows 1-{MAX_FACTS} facts"
        for fact in facts:
            text = " ".join(str((fact or {}).get("text", "")).split())
            if not text or len(text) > TEXT_MAX_CHARS:
                return f"every fact must be 1-{TEXT_MAX_CHARS} characters"
            if not (fact or {}).get("fact_id"):
                return "no fact id"
        return None

    def required_cues(self, data: dict) -> int:
        return 1

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _facts(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@mode_cache(maxsize=24)
def _facts(data_json: str, language: str, step: int) -> Image.Image:
    facts = json.loads(data_json)["facts"]
    page = Page(language)
    m = page.margin
    page.tracked(m, 160, T.label(language, "fact_card"), "mono", T.size("label"), T.rgb("amber"), 5)
    if step >= 1:
        px, lines, smallest = LAYOUT[len(facts)]
        top = 300
        for fact in facts:
            end = page.text(m + 56, top, page.W - 2 * m - 56, " ".join(fact["text"].split()), "body", px, T.rgb("ink"),
                            lines_max=lines, min_px=smallest, spacing=1.22)
            page.rect(m, top, 8, end - top, fill=T.rgb("amber"))
            top = end + 56
    return page.done()

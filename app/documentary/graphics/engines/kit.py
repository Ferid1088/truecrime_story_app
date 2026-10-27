"""What the eight cinematic engines share: the content item every case-specific text is (its words, the fact it comes
from, what kind of statement it is), the checks every engine runs on its data, and the pieces they are drawn from
(panels, basis tags, focus dimming). An engine decides only its own layout; look, limits and labels are config
(graphics.json `engines`, `labels`)."""

from __future__ import annotations

import json
from abc import abstractmethod

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Color, DoesNotFit, Page

#: basis -> colour name of its tag and rule
BASIS_COLOR = {"fact": "ink", "allegation": "amber", "witness": "blue", "expert": "blue", "finding": "ink",
               "disputed": "red", "unknown": "grey", "illustration": "grey"}
#: keys of an engine's data whose text is put into the film's language when the graphic is placed
TEXT_KEYS = ("text", "label", "charge", "outcome", "name", "key")
SEEN_OFTEN = 3


def settings() -> dict:
    return T.config().engines


def item_problem(item: object, what: str, limit: int | None = None, allowed: tuple[str, ...] | None = None,
                 illustration_ok: bool = False) -> str | None:
    """Why `item` ({text, fact_id, basis}) cannot be shown, or None. Every case-specific text names the stored fact it
    comes from; only an educational illustration has no fact and says so."""
    cfg = settings()
    if not isinstance(item, dict):
        return f"{what}: missing"
    text = " ".join(str(item.get("text", "")).split())
    if not text or len(text) > (limit or cfg["text_max_chars"]):
        return f"{what}: the text must be 1-{limit or cfg['text_max_chars']} characters"
    basis = item.get("basis")
    if basis not in cfg["bases"] or (allowed and basis not in allowed):
        return f"{what}: the kind of statement must be one of {list(allowed or cfg['bases'])}"
    if basis == "illustration":
        return None if illustration_ok and not item.get("fact_id") else f"{what}: an illustration is only allowed as the method"
    if not item.get("fact_id"):
        return f"{what}: no fact id (no statement without the fact it comes from)"
    return None


def items_problem(items: object, what: str, count: int, allowed: tuple[str, ...] | None = None,
                  minimum: int = 1) -> str | None:
    if not isinstance(items, list) or not minimum <= len(items) <= count:
        return f"{what}: {minimum} to {count} statements"
    for n, item in enumerate(items, 1):
        problem = item_problem(item, f"{what} {n}", allowed=allowed)
        if problem:
            return problem
    return None


def texts_of(node: object) -> list[tuple[dict, str]]:
    """(holder, key) of every text of an engine's data that has to be in the film's language."""
    out: list[tuple[dict, str]] = []
    if isinstance(node, dict):
        out += [(node, k) for k in TEXT_KEYS if isinstance(node.get(k), str) and node[k]]
        for k, v in node.items():
            if k not in TEXT_KEYS and isinstance(v, dict | list):
                out += texts_of(v)
    elif isinstance(node, list):
        for v in node:
            out += texts_of(v)
    return out


def fact_ids(node: object) -> list[str]:
    """Every fact id an engine's data stands on (its provenance), in order of appearance."""
    out: list[str] = []
    if isinstance(node, dict):
        if node.get("fact_id") and node["fact_id"] not in out:
            out.append(node["fact_id"])
        for v in node.values():
            if isinstance(v, dict | list):
                out += [f for f in fact_ids(v) if f not in out]
    elif isinstance(node, list):
        for v in node:
            out += [f for f in fact_ids(v) if f not in out]
    return out


def alpha_of(group: int, newest: int) -> int:
    """The newest group is bright; earlier ones are dimmed so the eye goes to what was just said."""
    return 255 if group >= newest else settings()["dim_alpha"]


def blend(color: Color, alpha: int) -> Color:
    ground = T.rgb("ground")
    k = alpha / 255
    return tuple(int(g + (c - g) * k) for c, g in zip(color, ground, strict=True))


def panel(page: Page, left: int, top: int, width: int, height: int, color: Color, alpha: int = 255,
          dashed: bool = False) -> None:
    """A card with a rule on its top edge (dashed outline instead of fill for something that is not known)."""
    if dashed:
        page.dashed_rect(left, top, width, height, blend(color, alpha), line=3)
        return
    page.rect(left, top, width, height, fill=T.rgb("card"), alpha=min(alpha, 235))
    page.rect(left, top, width, 8, fill=blend(color, alpha))


def basis_tag(page: Page, left: int, top: int, basis: str, alpha: int = 255, key: str | None = None,
              width: int = 0) -> int:
    """The small tag naming what kind of statement this is (wrapped when it is longer than `width`); returns the
    y below it."""
    color = blend(T.rgb(BASIS_COLOR.get(basis, "grey")), alpha)
    text = T.label(page.language, key or f"basis_{basis}")
    if width and page.measure(text, "mono", 24) + 4 * len(text) > width:
        return page.text(left, top, width, text, "mono", 22, color, lines_max=2, min_px=16, spacing=1.2)
    return page.tracked(left, top, text, "mono", 24, color, 4)


def within(y: int, limit: int, what: str) -> int:
    """`y` when the content ends above `limit`; a page whose content runs past its box is not drawn."""
    if y > limit:
        raise DoesNotFit(f"{what} runs {y - limit}px past its box")
    return y


def statement(page: Page, left: int, top: int, width: int, item: dict, alpha: int = 255, px: int = 52,
              lines_max: int = 4, min_px: int = 34, quoted: bool = False, tag: str | None = None,
              show_tag: bool = True) -> int:
    """A tag over the statement's text (left out when the box already says what kind of statement it holds);
    returns the y below."""
    y = basis_tag(page, left, top, item["basis"], alpha, tag, width) + 8 if show_tag else top
    text = " ".join(item["text"].split())
    if quoted:
        text = f"“{text}”" if page.language in ("en", "de") else f"«{text}»"
    return page.text(left, y, width, text, "body", px, blend(T.rgb("ink"), alpha), lines_max=lines_max,
                     min_px=min_px, spacing=1.32)


def heading(page: Page, label_key: str, title: str | None = None, title_px: int = 84, lines_max: int = 2) -> int:
    """The engine's name as a small spaced label and the point of the shot as a large title (the name itself when
    the shot has no other point); returns the y below."""
    label = T.label(page.language, label_key)
    if not title or title.casefold() == label.casefold():
        return page.text(page.margin, 96, page.width_inside(), label, "title", title_px, T.rgb("ink"), lines_max=1,
                         min_px=56, spacing=1.02, upper=True)
    page.tracked(page.margin, 88, label, "mono", T.size("label"), T.rgb("amber"), 5)
    return page.text(page.margin, 134, page.width_inside(), title, "title", title_px, T.rgb("ink"),
                     lines_max=lines_max, min_px=56, spacing=1.02, upper=True)


class Engine(Graphic):
    """A graphic of the eight engines: steps come from the data, drawing is `draw_state(step)`."""

    #: engine id kept with every plan and preview (bumped when the look or the data contract changes)
    version = 1

    @abstractmethod
    def steps(self, data: dict) -> list[str]:
        """The reveal steps of this data, in order (names for the preview and the inspector)."""

    def required_cues(self, data: dict) -> int:
        return len(self.steps(data))

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return self.draw(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)

    @abstractmethod
    def draw(self, data_json: str, language: str, step: int) -> Image.Image:
        """The page after `step` reveals; `data_json` is the sorted JSON so a page can be cached."""

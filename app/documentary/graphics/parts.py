"""Pieces the graphics share: the header (label + title), a card with a coloured top rule, a status pill."""

from __future__ import annotations

from app.documentary.graphics import theme as T
from app.documentary.graphics.page import Page


def header(page: Page, label: str, label_color, title: str, title_px: int | None = None, lines_max: int = 2,
           top: int = 88, width: int | None = None) -> int:
    """A small spaced label and a large condensed title, set in the card kind's own header style (stacked, with a rule
    under it, with the label on a filled tab, or centred); returns the y below it."""
    spec = T.theme_of(T.active())
    style = spec.header if spec is not None else "stack"
    full, label_px = width or page.width_inside(), T.size("label")
    align = "center" if style == "center" else "start"
    if label and style == "band":
        tab = page.measure(label, "mono", label_px) + 5 * len(label) + 48
        page.rect(page.margin, top - 12, tab, label_px + 26, fill=label_color)
        page.tracked(page.margin + 24, top, label, "mono", label_px, T.rgb("ground"), 5)
    elif label:
        page.tracked(page.margin, top, label, "mono", label_px, label_color, 5, align=align, width=full if align == "center" else 0)
    below = page.text(page.margin, top + (36 if label else 8), full, title, "title", title_px or T.size("title"),
                      T.rgb("ink"), align=align, lines_max=lines_max, min_px=60, spacing=1.02, upper=True)
    if style == "rule":
        page.rect(page.margin, below + 10, 220, 6, fill=label_color)
        return below + 30
    return below


def card(page: Page, left: int, top: int, width: int, height: int, rule_color, rule: int = 8, alpha: int = 255) -> None:
    page.rect(left, top, width, height, fill=T.rgb("card"), alpha=alpha)
    page.rect(left, top, width, rule, fill=rule_color)


def pill(page: Page, left: int, top: int, text: str, color, px: int = 26) -> int:
    """An outlined pill with a text label (colour is never the only signal); returns its width."""
    width = page.measure(text, "mono", px) + 48
    page.rect(left, top, width, px + 26, outline=color, line=3)
    page.text(left + 24, top + 11, width, text, "mono", px, color, lines_max=1)
    return width


def source_line(page: Page, left: int, top: int, width: int, text: str, px: int = 24) -> int:
    return page.text(left, top, width, text, "mono", px, T.rgb("grey"), lines_max=2, min_px=18, spacing=1.3)

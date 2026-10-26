"""Pieces the graphics share: the header (label + title), a card with a coloured top rule, a status pill."""

from __future__ import annotations

from app.documentary.graphics import theme as T
from app.documentary.graphics.page import Page


def header(page: Page, label: str, label_color, title: str, title_px: int | None = None, lines_max: int = 2,
           top: int = 88, width: int | None = None) -> int:
    """Small spaced label over a large condensed title; returns the y below the title."""
    if label:
        page.tracked(page.margin, top, label, "mono", T.size("label"), label_color, 5)
    return page.text(page.margin, top + (36 if label else 8), width or page.width_inside(), title, "title",
                     title_px or T.size("title"), T.rgb("ink"), lines_max=lines_max, min_px=60, spacing=1.02,
                     upper=True)


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

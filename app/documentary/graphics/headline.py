"""Newspaper headline card: a real headline of the time, with its newspaper and date."""

from __future__ import annotations

import json
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

from app.documentary.graphics import dates
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Page

SHEET_W, SHEET_H = 790, 720


def domain_of(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def known_source(url: str) -> str | None:
    """The configured newspaper name of an address (subdomains count), or None when it is not a listed paper."""
    names = T.config().headline["source_names"]
    host = domain_of(url)
    return next((name for domain, name in names.items() if host == domain or host.endswith("." + domain)), None)


class HeadlineCard(Graphic):
    kind = "headline"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().headline
        text = " ".join(str(data.get("headline", "")).split())
        if not text or len(text) > cfg["headline_max_chars"]:
            return f"the headline must be 1-{cfg['headline_max_chars']} characters"
        if not data.get("url") or known_source(data["url"]) is None:
            return "the address is not a listed newspaper"
        if data.get("source") != known_source(data["url"]):
            return "the newspaper name does not match its address"
        if not dates.valid(data.get("published")) or not data["published"].get("day"):
            return "the headline needs its full publication date"
        if not data.get("fact_id"):
            return "no fact id (the event the headline belongs to)"
        return None

    def required_cues(self, data: dict) -> int:
        return 1

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _headline(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


def date_line(published: dict, language: str) -> str:
    big, year = dates.display(published, language)
    return f"{big} {year}".strip()


@lru_cache(maxsize=24)
def _headline(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    page = Page(language)
    m = page.margin
    col = 640
    page.tracked(m, 170, T.label(language, "headline"), "mono", T.size("label"), T.rgb("amber"), 5)
    y = page.text(m, 222, col, T.label(language, "headline_title"), "title", T.size("title"), T.rgb("ink"),
                  lines_max=3, min_px=64, spacing=1.0, upper=True)
    page.hline(m, 800, col, T.rgb("rule"), 2)
    y = 822
    for key, value in ((T.label(language, "headline_source"), data["source"]),
                       (T.label(language, "published"), date_line(data["published"], language))):
        y = page.text(m, y, col, f"{key}: {value}", "mono", 27, T.rgb("grey"), lines_max=2, min_px=22, spacing=1.35)
    sheet = _sheet(data, language, step >= 1)
    x = m - 30 if page.rtl else page.W - m - SHEET_W + 30
    shadow = Image.new("RGBA", page.img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle((x + 6, 172, x + SHEET_W + 6, 150 + SHEET_H + 22), fill=(0, 0, 0, 160))
    page.img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))
    rotated = sheet.rotate(0.8, expand=True, resample=Image.BICUBIC)
    page.img.alpha_composite(rotated, (x, 150))
    return page.done()


def _sheet(data: dict, language: str, with_headline: bool) -> Image.Image:
    article = data.get("language") or language
    ink = T.rgb("paper_ink")
    sheet = Page(article, (SHEET_W, SHEET_H), (230, 224, 208))
    pad = 48
    inner = SHEET_W - 2 * pad
    sheet.hline(pad, 52, inner, ink, 5)
    sheet.hline(pad, 62, inner, ink, 2)
    y = sheet.text(pad, 84, inner, data["source"], "body_bold", 64, ink, align="center", lines_max=1, min_px=36,
                   spacing=1.1)
    sheet.hline(pad, y + 8, inner, ink, 2)
    sheet.hline(pad, y + 16, inner, ink, 5)
    sheet.text(pad, y + 34, inner, date_line(data["published"], article), "mono", 24, (96, 90, 82), align="center",
               lines_max=1)
    if with_headline:
        sheet.text(pad, y + 120, inner, " ".join(data["headline"].split()), "body_bold", 62, ink, lines_max=6,
                   min_px=36, spacing=1.12)
    sheet.hline(pad, SHEET_H - 90, inner, ink, 2)
    sheet.text(pad, SHEET_H - 72, inner, domain_of(data["url"]), "mono", 22, (96, 90, 82), lines_max=1)
    return sheet.done().convert("RGBA")

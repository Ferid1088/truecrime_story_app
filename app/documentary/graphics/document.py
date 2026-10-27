"""Document card: the exact words of a court or police document on screen while the narrator reads them."""

from __future__ import annotations

import json
import re

from PIL import Image, ImageDraw, ImageFilter

from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic, ease
from app.documentary.graphics.page import Page, mode_cache, rotated_words
from app.documentary.visuals.shaping import RTL

PAPER_W, PAPER_H = 1000, 860
QUOTE_MARKS = {"en": ("“", "”"), "de": ("„", "“"), "fa": ("«", "»"),
               "ar": ("«", "»")}


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def verify_verbatim(document_text: str, quote: str) -> bool:
    """The quote must occur in the stored document text character for character (whitespace aside)."""
    return bool(quote) and normalized(quote) in normalized(document_text)


@mode_cache(maxsize=16)
def _built(data_json: str, language: str, highlighted: bool) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """(page, absolute box of the quote band) - the same layout serves a state and the wipe between two."""
    return DocumentCard().build(json.loads(data_json), language, highlighted)


def _key(data: dict) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


class DocumentCard(Graphic):
    kind = "document"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().document_card
        doc, quote = data.get("document") or {}, data.get("quote") or {}
        if not doc.get("id"):
            return "no stored source document id"
        if not (doc.get("type") and doc.get("issuer")):
            return "the document has no type or issuer"
        text = normalized(quote.get("text", ""))
        if not text or len(text) > cfg["quote_max_chars"]:
            return f"the quote must be 1-{cfg['quote_max_chars']} characters"
        if not quote.get("fact_id"):
            return "the quote has no fact id"
        if quote.get("verified") is not True:
            return "the quote was not checked against the stored document"
        if data.get("redactions") and doc.get("language", "en") in RTL:
            return "redaction bars are not drawn on right-to-left documents"
        return None

    def required_cues(self, data: dict) -> int:
        return 1

    # -- drawing ---------------------------------------------------------
    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _built(_key(data), language, step >= 1)[0]

    def draw_between(self, data, language, step, progress, before, after):
        """The highlight wipes across the quote: only the quote's band differs between the two states."""
        x, y, w, h = _built(_key(data), language, True)[1]
        reach = int(w * ease(progress))
        out = before.copy()
        if reach > 0:
            rtl = T.is_rtl(data["document"].get("language") or language)
            box = (x + w - reach, y, x + w, y + h) if rtl else (x, y, x + reach, y + h)
            out.paste(after.crop(box), box[:2])
        return out

    def build(self, data: dict, language: str, highlighted: bool):
        page = Page(language)
        m = page.margin
        col = 620
        page.tracked(m, 118, T.label(language, "document_card"), "mono", T.size("label"), T.rgb("amber"), 5)
        y = page.text(m, 160, col, T.label(language, "document_title"), "title", 124,
                      T.rgb("ink"), lines_max=2, min_px=72, spacing=1.0, upper=True)
        page.text(m, y + 24, col, T.label(language, "document_sub"), "body", 38, (196, 191, 180),
                  lines_max=4, min_px=30, spacing=1.3)
        doc = data["document"]
        page.hline(m, 808, col, T.rgb("rule"), 2)
        lines = [(T.label(language, "source"), doc["issuer"]), (T.label(language, "filed"), doc.get("date", "")),
                 (T.label(language, "page"), str(doc.get("page", "")))]
        y = 838
        for key, value in lines:
            if value:
                y = page.text(m, y, col, f"{key}: {value}".upper() if not page.rtl else f"{key}: {value}", "mono", 26,
                              T.rgb("grey"), lines_max=2, min_px=20, spacing=1.55)
        paper, band = self._paper(data, language, highlighted)
        x, y = self._place_paper(page, paper, 100)
        return page.done(), (x + band[0], y + band[1], band[2], band[3])

    def _place_paper(self, page: Page, paper: Image.Image, top: int) -> tuple[int, int]:
        rotated = paper.rotate(-T.config().document_card["rotate_degrees"], expand=True, resample=Image.BICUBIC)
        x = page.W - 800 - rotated.width if page.rtl else 800
        shadow = Image.new("RGBA", page.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rectangle((x + 6, top + 22, x + rotated.width + 6, top + rotated.height + 22),
                                         fill=(0, 0, 0, 160))
        page.img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))
        page.img.alpha_composite(rotated, (x, top))
        page.d = ImageDraw.Draw(page.img)
        page.words.extend(rotated_words(paper.words, paper.size, -T.config().document_card["rotate_degrees"], (x, top)))
        return x, top

    def _paper(self, data: dict, language: str, highlighted: bool) -> tuple[Image.Image, tuple[int, int, int, int]]:
        doc, quote = data["document"], data["quote"]
        dlang = doc.get("language") or language
        ink = T.rgb("paper_ink")
        sheet = Page(dlang, (PAPER_W, PAPER_H), T.rgb("paper"))
        pad = 60
        inner = PAPER_W - 2 * pad
        sheet.text(pad, 62, inner - 230, doc["type"], "mono_bold", 28, ink, lines_max=2, min_px=20, upper=True,
                   spacing=1.2)
        if doc.get("case_number"):
            sheet.text(pad, 62, inner, f"NO. {doc['case_number']}", "mono", 24, ink, align="end", lines_max=1)
        sheet.hline(pad, 134, inner, ink, 4)
        y = 178
        grey = (118, 110, 100)
        for line in data.get("context_before", [])[:3]:
            y = self._context(sheet, pad, y, inner, line, data.get("redactions", []), grey)
        y += 14
        opening, closing = QUOTE_MARKS.get(dlang, QUOTE_MARKS["en"])
        text = f"{opening}{normalized(quote['text'])}{closing}"
        lines, px = sheet.lines(text, "body_italic", 50, inner - 70, lines_max=4, min_px=32)
        height = int(len(lines) * px * 1.38) + 48
        band = (pad - 8, y - 8, inner + 16, height)
        if highlighted:
            sheet.rect(pad - 8, y - 8, inner + 16, height, fill=(238, 200, 122))
        y = sheet.text(pad + 28, y + 14, inner - 70, text, "body_italic", px, ink, lines_max=4, spacing=1.38)
        y += 40
        for line in data.get("context_after", [])[:3]:
            y = self._context(sheet, pad, y, inner, line, data.get("redactions", []), grey)
        if doc.get("exhibit"):
            self._stamp(sheet, T.label(language, "exhibit"))
        out = sheet.done().convert("RGBA")
        out.words = sheet.words
        return out, (band[0], band[1], band[2], band[3])

    def _context(self, sheet: Page, pad: int, y: int, inner: int, text: str, redactions: list[str],
                 color) -> int:
        """A line of the document before/after the quote, wrapped word by word; redacted names are black bars."""
        px = 28
        marks = {}
        for n, secret in enumerate(sorted(redactions, key=len, reverse=True)):
            token = f"\u2063{n}\u2063"
            marks[token] = secret
            text = text.replace(secret, token)
        font = T.font(sheet.language, "mono", px)
        space = sheet.measure(" ", "mono", px)
        x = pad
        for word in text.split():
            secret = next((v for k, v in marks.items() if k in word), None)
            shown = word.replace(next(k for k, v in marks.items() if v == secret), "") if secret else word
            width = sheet.measure(secret, "mono", px) if secret else sheet.measure(word, "mono", px)
            if x + width > pad + inner and x > pad:
                y += int(px * 1.6)
                x = pad
            if secret:
                sheet.rect(x, y + 2, width, px + 2, fill=T.rgb("redaction"))
                tail = sheet.measure(shown, "mono", px) if shown else 0
                if shown:
                    sheet.put(sheet.x(x + width, tail), y, shown, font, color, tail)
                width += tail
            else:
                sheet.put(sheet.x(x, width), y, word, font, color, width)
            x += width + space
        return y + int(px * 2.1)

    def _stamp(self, sheet: Page, text: str) -> None:
        w, h = 300, 88
        layer = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        red = (176, 52, 44, 235)
        d.rectangle((10, 10, w + 10, h + 10), outline=red, width=6)
        f = T.font("en", "mono_bold", 48)
        tw = f.getbbox(text)[2]
        d.text((10 + (w - tw) / 2, 20), text, font=f, fill=red)
        layer = layer.rotate(7, expand=True, resample=Image.BICUBIC)
        sheet.img.alpha_composite(layer, (PAPER_W - layer.width - 40, PAPER_H - layer.height - 40))
        sheet.d = ImageDraw.Draw(sheet.img)

"""Title normalization for collision checks (Latin, Persian, Arabic).

`norm_title` is for comparing only; display text is never altered."""

from __future__ import annotations

import re
import unicodedata

_FOLD = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ؤ": "و", "ئ": "ی", "ھ": "ه", "ہ": "ه", "ۀ": "ه", "ß": "ss", "ı": "i", "ø": "o",
    "đ": "d", "ł": "l", "æ": "ae", "œ": "oe",
})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# zero-width / bidi marks; ZWNJ (half-space) becomes a space so
# "حل‌نشده" == "حل نشده"
_INVISIBLE = dict.fromkeys(map(ord, "​‍‎‏‪‫‬‭‮"
                               "⁠⁦⁧⁨⁩﻿"), None)
_SPACE = re.compile(r"\s+")


def norm_title(text: str | None) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("‌", " ").translate(_INVISIBLE)
    t = t.casefold().translate(_FOLD).translate(_DIGITS)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn" and ch != "ـ")
    t = "".join(" " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in t)
    return _SPACE.sub(" ", t).strip()


def tokens(text: str | None) -> list[str]:
    return norm_title(text).split()

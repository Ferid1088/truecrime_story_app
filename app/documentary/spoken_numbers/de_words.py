"""German number words: cardinals, ordinals, years and digit strings.

Written here instead of taken from a library because the narrator needs control over the forms a library fixes: "ein/eine/einen/einem" for a 1 before a
noun, "hundert" without "ein", years in hundreds, and the big-number nouns (Million, Milliarde, Billion are the long scale: a Milliarde is 10^9).
"""

from __future__ import annotations

UNITS = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn",
         "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
TENS = {2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig", 6: "sechzig", 7: "siebzig", 8: "achtzig", 9: "neunzig"}
# The big-number nouns, long scale: (singular, plural), by group of three digits above thousand.
BIG_NOUNS = [("Million", "Millionen"), ("Milliarde", "Milliarden"), ("Billion", "Billionen"), ("Billiarde", "Billiarden")]
MAX_CARDINAL = 10 ** 15 - 1
ORDINAL_STEMS = {"eins": "erst", "drei": "dritt", "sieben": "siebt"}


def _below_hundred(n: int, one: str) -> str:
    if n < 20:
        return one if n == 1 else UNITS[n]
    tens, unit = divmod(n, 10)
    return TENS[tens] if unit == 0 else f"{'ein' if unit == 1 else UNITS[unit]}und{TENS[tens]}"


def _below_thousand(n: int, one: str) -> str:
    hundreds, rest = divmod(n, 100)
    head = "" if hundreds == 0 else ("hundert" if hundreds == 1 else f"{UNITS[hundreds]}hundert")
    return head + (_below_hundred(rest, one) if rest else "")


def _below_million(n: int, one: str) -> str:
    thousands, rest = divmod(n, 1000)
    if thousands == 0:
        return _below_thousand(rest, one)
    head = "tausend" if thousands == 1 else _below_thousand(thousands, "ein") + "tausend"
    return head + (_below_thousand(rest, one) if rest else "")


def cardinal(n: int, one: str = "eins") -> str:
    """`n` in words, below 10^15; `one` is the form of a closing "eins" ("ein", "eine", "einen", "einem", "einer" before a noun). The part below a million is
    one word ("dreitausendvierhundert"); Million and above are nouns with their own words ("eine Million", "zwei Millionen")."""
    if n == 0:
        return UNITS[0]
    rest, low = divmod(n, 1000000)
    bigs = []
    for _ in BIG_NOUNS:
        rest, group = divmod(rest, 1000)
        bigs.append(group)
    parts = []
    for index in range(len(bigs) - 1, -1, -1):
        if bigs[index]:
            count = bigs[index]
            noun = BIG_NOUNS[index][0 if count == 1 else 1]
            parts.append(f"{'eine' if count == 1 else _below_thousand(count, 'ein')} {noun}")
    if low or not parts:
        parts.append(_below_million(low, one))
    return " ".join(parts)


def ordinal(n: int, ending: str = "e") -> str | None:
    """`n` as an ordinal word with the case `ending` ("e", "en", "er", "es", "em"): erste, dritte, siebte, achte, zwanzigste, einundzwanzigste. None above
    999 999 (no narrator says it, the caller keeps the cardinal)."""
    if not 0 < n < 1000000:
        return None
    words = cardinal(n)
    last_two = n % 100
    if last_two == 0 or last_two >= 20:
        return words + "st" + ending
    for tail, stem in ORDINAL_STEMS.items():
        if words.endswith(tail):
            return words[: -len(tail)] + stem + ending
    return words + ("" if words.endswith("acht") else "t") + ending


def year(n: int, pairs: bool = False) -> str:
    """A year as a narrator says it: 1100-1999 in hundreds ("neunzehnhundertneunundneunzig"), everything else as a plain cardinal ("zweitausendsieben");
    with `pairs` 2010-2099 are said like the older ones, in two parts ("zwanzig vierundzwanzig")."""
    if pairs and 2010 <= n <= 2099:
        return f"{cardinal(n // 100)} {cardinal(n % 100)}"
    if 1100 <= n <= 1999:
        hundreds, rest = divmod(n, 100)
        return cardinal(hundreds) + "hundert" + (cardinal(rest) if rest else "")
    return cardinal(n)


def digits(text: str, two: str = "zwei") -> str:
    """Each digit of `text` as its word, separated by spaces ("110" -> "eins eins null"); `two` is "zwei" or the telephone form "zwo"."""
    return " ".join(two if char == "2" else UNITS[int(char)] for char in text if char.isdigit())

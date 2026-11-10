"""English number words: cardinals, ordinals, years and digit strings, written here so the narrator's habits are options and not a library's fixed choice
(American "one hundred twenty" or British "one hundred and twenty", "nineteen oh five", "twenty ten", a leading zero as "oh" or "zero")."""

from __future__ import annotations

import re

UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}
# Short scale: a billion is a thousand million, as in American and (nearly always) British English.
BIG_NOUNS = ["thousand", "million", "billion", "trillion", "quadrillion"]
MAX_CARDINAL = 10 ** 18 - 1
LAST_WORD = re.compile(r"(.*[ -])?(\w+)$")
ORDINAL_ENDINGS = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}


def _below_hundred(n: int) -> str:
    if n < 20:
        return UNITS[n]
    tens, unit = divmod(n, 10)
    return TENS[tens] if unit == 0 else f"{TENS[tens]}-{UNITS[unit]}"


def _below_thousand(n: int, with_and: bool) -> str:
    hundreds, rest = divmod(n, 100)
    if hundreds == 0:
        return _below_hundred(rest)
    head = f"{UNITS[hundreds]} hundred"
    if not rest:
        return head
    return f"{head} and {_below_hundred(rest)}" if with_and else f"{head} {_below_hundred(rest)}"


def cardinal(n: int, with_and: bool = False) -> str:
    """`n` in words below 10^18 ("one hundred twenty"; with `with_and` the British "one hundred and twenty", "one thousand and five")."""
    if n == 0:
        return UNITS[0]
    groups = []
    while n:
        n, group = divmod(n, 1000)
        groups.append(group)
    parts = [f"{_below_thousand(group, with_and)} {BIG_NOUNS[index - 1]}" if index else _below_thousand(group, with_and)
             for index, group in reversed(list(enumerate(groups))) if group]
    if with_and and len(groups) > 1 and 0 < groups[0] < 100:
        parts.insert(len(parts) - 1, "and")
    return " ".join(parts)


def ordinal(n: int, with_and: bool = False) -> str:
    """"first", "third", "twenty-first", "one hundredth": the last word of the cardinal takes the ending."""
    head, last = LAST_WORD.match(cardinal(n, with_and)).groups("")
    if last in ORDINAL_ENDINGS:
        last = ORDINAL_ENDINGS[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + last


def plural(words: str) -> str:
    """The plural of a decade or century name: "nineties", "hundreds", "tens", "thousands"."""
    return words[:-1] + "ies" if words.endswith("y") else words + "s"


def pair(n: int, zero: str = "oh") -> str:
    """Two digits as a pair of a year or an address: "ninety-nine", "oh five", "twenty"."""
    return f"{zero} {UNITS[n]}" if n < 10 else _below_hundred(n)


def year(n: int, style_2000s: str = "two_thousand", style_after_2009: str = "pairs", with_and: bool = False) -> str:
    """A year as a narrator says it: 1100-1999 as two pairs ("nineteen ninety-nine", "nineteen oh five", "nineteen hundred"), 1000-1099 as "ten sixty-six",
    2000-2009 as "two thousand five" (or "twenty oh five"), 2010-2099 as "twenty ten" (or "two thousand ten"); anything else is a plain cardinal."""
    high, low = divmod(n, 100)
    if 2000 <= n <= 2009 and (style_2000s == "two_thousand" or n == 2000):
        return cardinal(n, with_and)
    if 2010 <= n <= 2099 and style_after_2009 == "two_thousand":
        return cardinal(n, with_and)
    if 1000 <= n <= 2099 and n % 1000 != 0:
        return f"{cardinal(high)} hundred" if low == 0 else f"{cardinal(high)} {pair(low)}"
    return cardinal(n, with_and)


def digits(text: str, zero: str = "oh", doubles: bool = False) -> str:
    """Each digit of `text` as its word ("911" -> "nine one one", "0150" -> "oh one five oh"); with `doubles` a repeated digit is "double five" or "triple five"."""
    chars = [char for char in text if char.isdigit()]
    words, index = [], 0
    while index < len(chars):
        char, run = chars[index], 1
        while doubles and index + run < len(chars) and chars[index + run] == char and run < 3:
            run += 1
        word = zero if char == "0" else UNITS[int(char)]
        words.append({1: word, 2: f"double {word}", 3: f"triple {word}"}[run])
        index += run
    return " ".join(words)


def house_number(n: int) -> str:
    """A number of a room, flight, route or house as people say it: 304 "three oh four", 370 "three seventy", 4521 "forty-five twenty-one", 1200 "twelve hundred",
    100 "one hundred"; above 9999 the plain cardinal."""
    if n < 100 or n > 9999 or n % 1000 == 0:
        return cardinal(n)
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{UNITS[hundreds]} hundred" if rest == 0 else f"{UNITS[hundreds]} {pair(rest)}"
    high, low = divmod(n, 100)
    return f"{cardinal(high)} hundred" if low == 0 else f"{cardinal(high)} {pair(low)}"

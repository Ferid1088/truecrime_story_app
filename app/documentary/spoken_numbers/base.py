"""What every language's number reader shares: the interface and the text that must never be read as a number."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

# Text with digits that is not a number to say: a web address, an e-mail, a code that starts with a letter ("A1", "mp3", "G20"), a capitalised code
# with a hyphen ("COVID-19", "F-16", "B-52"), a size ("4x4") and a one-letter suffix in capitals ("3D", "5G"). Such a span is set aside before the numbers
# are read and put back as it was. A number glued to a word that is a unit or an ending ("9mm", "100km", "35enne", "10h30", German "35-Jährige") is a
# number: it is not protected.
PROTECTED = re.compile(
    r"https?://\S+|www\.\S+|\S+@\S+\.\S+"
    r"|\b[A-ZÀ-Þ][A-ZÀ-Þ0-9]*-\d+\b"
    r"|\b[A-Za-zÀ-ÿ]+\d[A-Za-zÀ-ÿ0-9]*\b"
    r"|\b\d+[xX]\d+\b|\b\d+[DG]\b")
PLACEHOLDER = "{}"
PLACEHOLDER_PATTERN = re.compile("(\\d+)")


class LanguageNumbers(ABC):
    """The numbers of a text in the words of one language, for the voice. Subclasses live in `<language>.py` of this package and are named `Numbers`;
    `options` is the language's block of config voice.number_words."""

    def __init__(self, options: dict, max_value: int):
        self.options, self.max_value = options, max_value

    @abstractmethod
    def convert(self, text: str) -> str:
        """The text with its numbers in words (the protected spans already set aside)."""

    def text(self, text: str) -> str:
        kept: list[str] = []

        def keep(match: re.Match) -> str:
            kept.append(match.group())
            return PLACEHOLDER.format(len(kept) - 1)

        converted = self.convert(PROTECTED.sub(keep, text))
        return PLACEHOLDER_PATTERN.sub(lambda m: kept[int(m.group(1))], converted)

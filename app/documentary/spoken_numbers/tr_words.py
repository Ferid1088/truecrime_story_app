"""The words of Turkish numbers: cardinals (written apart, "dokuz yüz on bir"), digit by digit, decimals, and the letters of a plate."""

from __future__ import annotations

ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
SCALES = ["", "bin", "milyon", "milyar", "trilyon", "katrilyon", "kentilyon"]
DIGIT_WORDS = ["sıfır", *ONES[1:]]
LETTER_NAMES = {
    "A": "a", "B": "be", "C": "ce", "Ç": "çe", "D": "de", "E": "e", "F": "fe", "G": "ge", "Ğ": "yumuşak ge", "H": "he", "I": "ı", "İ": "i", "J": "je",
    "K": "ke", "L": "le", "M": "me", "N": "ne", "O": "o", "Ö": "ö", "P": "pe", "R": "re", "S": "se", "Ş": "şe", "T": "te", "U": "u", "Ü": "ü",
    "V": "ve", "Y": "ye", "Z": "ze", "W": "çift ve", "X": "iks", "Q": "ku"}


def group_words(value: int) -> list[str]:
    """A number under 1000: "yüz" and not "bir yüz"."""
    hundreds, rest = divmod(value, 100)
    words = [*([ONES[hundreds]] if hundreds > 1 else []), *(["yüz"] if hundreds else [])]
    tens, ones = divmod(rest, 10)
    return [*words, *([TENS[tens]] if tens else []), *([ONES[ones]] if ones else [])]


def cardinal(value: int) -> list[str]:
    """The cardinal in words: "bin" and not "bir bin", but "bir milyon" and "bir milyar"; 0 is "sıfır"."""
    if value == 0:
        return ["sıfır"]
    words: list[str] = []
    groups = []
    while value:
        value, group = divmod(value, 1000)
        groups.append(group)
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if not group:
            continue
        if index == 1 and group == 1:
            words.append("bin")
        else:
            words += [*group_words(group), *([SCALES[index]] if index else [])]
    return words


def digits(text: str) -> list[str]:
    """Digit by digit: a code, a phone number, a figure too long to be a quantity."""
    return [DIGIT_WORDS[int(char)] for char in text if char.isdigit()]


def decimals(fraction: str) -> list[str]:
    """What follows "virgül": up to two digits as a number with its zeros ("05" is "sıfır beş"), longer fractions digit by digit."""
    if len(fraction) > 2:
        return digits(fraction)
    leading_zeros = len(fraction) - len(fraction.lstrip("0"))
    return [*digits("0" * leading_zeros), *(cardinal(int(fraction)) if fraction.strip("0") else [])]


def capitalized(word: str) -> str:
    return ("İ" if word.startswith("i") else word[0].upper()) + word[1:]

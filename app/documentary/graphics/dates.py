"""Dates as the graphics show them: exactly the precision the fact states (a day only if the fact has one)."""

from __future__ import annotations

from app.documentary.visuals.generated import _FA_DIGITS, MONTHS


def localized_number(value: int | str, language: str) -> str:
    text = str(value)
    return text.translate(_FA_DIGITS) if language == "fa" else text


def display(date: dict, language: str) -> tuple[str, str]:
    """(big text, year) of {"year", "month"?, "day"?}: 'NOV 13' + '2022', 'MAY' + '2023', '2025' + ''."""
    year = localized_number(date["year"], language)
    month = date.get("month")
    if not month:
        return year, ""
    name = MONTHS[language][int(month) - 1]
    if language in ("en", "de"):
        name = name[:3].upper() if language == "en" else name[:3].upper() + ("" if name[:3] == "Mai" else "")
    day = date.get("day")
    if not day:
        return name, year
    number = localized_number(day, language)
    if language == "en":
        return f"{name} {number}", year
    if language == "de":
        return f"{number}. {name}", year
    return f"{number} {name}", year


def valid(date: object) -> bool:
    if not isinstance(date, dict) or not isinstance(date.get("year"), int) or not 1000 <= date["year"] <= 2999:
        return False
    month, day = date.get("month"), date.get("day")
    return (month is None or 1 <= month <= 12) and (day is None or (month is not None and 1 <= day <= 31))

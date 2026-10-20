"""The fixed identity line of the host ("I'm Fereidoun, and here I tell you ..."): the last words
of the host's opening in every long film and the last words of every short. The words are
owner-approved config (shorts_host_led.identity_line); no agent writes or rewrites them."""

from __future__ import annotations

from app.core.ai_config import ai_config


def identity_line(language: str) -> str:
    return ai_config.shorts_host_led.identity_line[language]


def ends_with_identity(text: str, language: str) -> bool:
    return text.rstrip().endswith(identity_line(language))


def with_identity(text: str, language: str) -> str:
    """``text`` with the identity line as its last words (once, however often this is called)."""
    text = text.strip()
    if ends_with_identity(text, language):
        return text
    return f"{text} {identity_line(language)}"


def strip_identity(text: str, language: str) -> str:
    text = text.strip()
    if not ends_with_identity(text, language):
        return text
    return text[: len(text) - len(identity_line(language))].rstrip()

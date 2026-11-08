"""Numbers in the words of the film's language, for the voice: a voice reads bare digits one by one ("911" as nine, one, one). Each language in config
voice.number_words has a module here (`es.py`, `pt.py`, ...) with a `Numbers` class (base.LanguageNumbers) that turns the digits of the text the voice is
given, and of the text the speech check expects, into the words a native narrator says. The story and the display keep their digits."""

from __future__ import annotations

from importlib import import_module

from app.documentary.spoken_numbers.base import LanguageNumbers


def reader_for(language: str, options: dict, max_value: int) -> LanguageNumbers:
    return import_module(f"app.documentary.spoken_numbers.{language}").Numbers(options, max_value)

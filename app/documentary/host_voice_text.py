"""The host's words prepared for the voice, the way the narration's blocks are: the words the language's voice reads wrongly are
spelled the way it reads them right; the host writer sets the punctuation. The host
keeps its persona and its words: the text the avatar and the screen show (`scene.text`) is never changed; only what the voice is given is."""

from __future__ import annotations

import re

from app.core.ai_config import ai_config
from app.db.models import HostScene


def block_id(scene: HostScene) -> str:
    return f"host{scene.id}"


def respelled(text: str, language: str) -> str:
    """The text with the words the language's voice reads wrongly spelled the way it reads them right (config host.voice_respellings)."""
    for word, spelling in ai_config.host.voice_respellings.get(language, {}).items():
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", spelling, text)
    return text


def prepare(scene: HostScene) -> str | None:
    """The text the voice is given for the host's appearance (None when no word of it is respelled)."""
    spoken = respelled(scene.text, scene.language)
    return spoken if spoken != scene.text else None

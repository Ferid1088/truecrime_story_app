"""The host's words prepared for the voice, the way the narration's blocks are (config host.text_preparation): the punctuation layer of the language. The host
keeps its persona and its words: the text the avatar and the screen show (`scene.text`) is never changed; only what the voice is given is."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import HostScene
from app.documentary.punctuation import stage as PS


def block_id(scene: HostScene) -> str:
    return f"host{scene.id}"


def respelled(text: str, language: str) -> str:
    """The text with the words the language's voice reads wrongly spelled the way it reads them right (config host.voice_respellings)."""
    for word, spelling in ai_config.host.voice_respellings.get(language, {}).items():
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", spelling, text)
    return text


async def prepare(db: Session, scene: HostScene, punctuation: PS.PunctuationStage | None = None) -> str | None:
    """The text the voice is given for the host's appearance (None when the language is not prepared). `punctuation` is the stage to use (default: the
    language's own)."""
    language = scene.language
    if language not in ai_config.host.text_preparation:
        spoken = respelled(scene.text, language)
        return spoken if spoken != scene.text else None
    text = scene.text
    if ai_config.semantic_punctuation.covers(language):
        marked = await (punctuation or PS.PunctuationStage(db, scene.case_id, language=language)).block({"block_id": block_id(scene), "text": text})
        text = marked.layer.text
    return respelled(text, language)

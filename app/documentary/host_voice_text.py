"""The host's words prepared for the voice, the way the narration's blocks are (config host.text_preparation): the reading text of the language,
where it has one (Persian's PPIE reading, Arabic's Tashkeel); the host writer sets the punctuation. The host
keeps its persona and its words: the text the avatar and the screen show (`scene.text`) is never changed; only what the voice is given is."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, HostScene, StoryVersion
from app.documentary.reading import stage as RS
from app.documentary.reading.block import BlockInput


def block_id(scene: HostScene) -> str:
    return f"host{scene.id}"


def respelled(text: str, language: str) -> str:
    """The text with the words the language's voice reads wrongly spelled the way it reads them right (config host.voice_respellings)."""
    for word, spelling in ai_config.host.voice_respellings.get(language, {}).items():
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", spelling, text)
    return text


async def prepare(db: Session, scene: HostScene, renderer, reader=None) -> str | None:
    """The text the voice is given for the host's appearance (None when the language is not prepared). The reading of the language, if it has one, is put
    on the renderer (`voicing`, and Persian's `planner`), which gives each word to the voice as the reading says. `reader` is the
    stage to use (default: the language's own)."""
    language = scene.language
    renderer.voicing = renderer.planner = None
    if language not in ai_config.host.text_preparation:
        spoken = respelled(scene.text, language)
        return spoken if spoken != scene.text else None
    bid, text = block_id(scene), scene.text
    if RS.required(language):
        item = BlockInput(bid, "host", text)
        version, case = db.get(StoryVersion, scene.story_version_id), db.get(Case, scene.case_id)
        read = await (reader or RS.read_items)(db, case, version, language, [item])
        renderer.voicing = await RS.voicing_of(db, language, read, [item])
        if ai_config.pronunciation_risk.enabled and RS.engine(language) == "persian":
            from app.documentary.pronunciation_planner import Planner

            voice = ai_config.voice.for_language(language)
            renderer.planner = Planner(db, language, voice.voice_id, voice.model_id)
    return respelled(text, language)

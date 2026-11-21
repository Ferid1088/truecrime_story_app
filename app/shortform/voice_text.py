"""The texts of the Shorts written for the voice. A Short is published as a hook, the story and the avatar's line; the voice reads exactly what is written, so one request per language gives every Short's three
texts to the spoken writer (the film's own writer, with the language's style guide and number rules) and the voice director's punctuation rules: every sentence ends with a mark and the marks are set
while writing. Checked afterwards in code (nothing empty, no foreign script, no digits where numbers are words, no run of words without a mark, the invitation kept); a text that fails keeps the
published words with a mark at its end, and the answer says so."""

from __future__ import annotations

import json
import re

from app.agents.runner import run_agent
from app.agents.shorts import extract_json
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.documentary import spoken as SP
from app.documentary.host import punctuation_for_the_voice
from app.providers.generation.base import GenerationError
from app.shortform.pack import SPOKEN, _booked, _invitation

ENDS = ".?!…؟"
TASK_LINE = "Rewrite the Shorts below for the voice, as the task in your instructions says. Return the JSON only."
_WORD = re.compile(r"[\w’'-]+")


def system_text(language: str) -> str:
    """The spoken writer's prompt for the language, its punctuation rules, and the task of the Shorts."""
    rules = punctuation_for_the_voice(language)
    return f"{SP.writer_system_prompt(language)}\n\n{rules}\n\n{prompt('shortform/voice_rewriter')}" if rules else f"{SP.writer_system_prompt(language)}\n\n{prompt('shortform/voice_rewriter')}"


def with_end(text: str) -> str:
    """The text with a mark at its end (a full stop when it has none)."""
    text = text.strip()
    return text if not text or text[-1] in ENDS else f"{text}."


def problems(language: str, field: str, text: str) -> list[str]:
    """What is wrong with one rewritten text for the voice."""
    found = []
    if not text.strip():
        return ["empty"]
    if SP.latin_left(text, language):
        found.append("foreign_script")
    if SP.digits_left(text, language):
        found.append("digits")
    if text.strip()[-1] not in ENDS:
        found.append("no_final_mark")
    limit = ai_config.spoken.max_sentence_words.get(language, 24) + ai_config.creative_pack.voice_run_slack
    if any(len(_WORD.findall(run)) > limit for run in re.split(r"[.?!…؟]+", text)):
        found.append("sentence_without_mark")
    if field == "avatar" and _invitation(language) and _letters(_invitation(language)) not in _letters(text):
        found.append("invitation_lost")
    return found


def _letters(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


async def rewrite(gen, language: str, shorts: dict[str, dict], *, ledger=None) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """(the voice texts {clip key: {hook, story, avatar}}, what made a text keep the published words {"C1/story": [reasons]}): ONE request for all the Shorts of the language."""
    request = {"language": SP.LANG_NAMES.get(language, language), "shorts": {k: {f: s[f].removesuffix(_invitation(language)).strip() if f == "avatar" else s[f] for f in SPOKEN} for k, s in shorts.items()}}
    answer: dict = {}
    for attempt in (1, 2, 3):                                # an answer that is not JSON is asked for again, twice at most (each request is booked)
        result = await _booked(ledger, f"Voice texts: {language}" + (f" (again {attempt - 1})" if attempt > 1 else ""), "spoken_writer",
                               lambda: run_agent("spoken_writer", gen, f"{TASK_LINE}\n{json.dumps(request, ensure_ascii=False)}", system=system_text(language)))
        if answer := extract_json(result.text):
            break
    if not answer:
        raise GenerationError("invalid_output", f"the spoken writer gave no JSON for {language} in three requests; the last answer began: {result.text[:200]!r}", provider=result.provider, model=result.model)
    out, kept = {}, {}
    for key, short in shorts.items():
        written = answer.get(key) if isinstance(answer.get(key), dict) else {}
        out[key] = {}
        for field in SPOKEN:
            text = " ".join(str(written.get(field) or "").split())
            if field == "avatar" and text and _invitation(language) and _letters(_invitation(language)) not in _letters(text):
                text = f"{text} {_invitation(language)}"                                  # the invitation always closes the avatar line
            if why := problems(language, field, text):
                kept[f"{key}/{field}"] = why
                text = with_end(shorts[key][field])
            out[key][field] = text
    return out, kept

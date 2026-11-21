"""The short clips of a film as the rest of the application reads them. A Short is written as ONE free narration (see `pack`); the voice, the avatar and the video steps still work with five
parts (hook, context, detail, reaction, cta), so `legacy_parts` cuts a narration into them at its own sentence and clause ends: a compatibility adapter that never constrains what is written."""

from __future__ import annotations

import itertools
import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ShortClipsConfig, ai_config
from app.db.models import ShortClip

TEXT_PARTS = ("hook", "context", "detail", "reaction", "cta")
SHARES = (0.25, 0.375, 0.75, 0.9)          # where the narration is cut: after the hook, the context, the detail and the reaction (the rest is the closing words)
_SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+")
_CLAUSE = re.compile(r"[;؛:,،—–]$")


def narration_seconds(text: str, language: str) -> float:
    """The estimate of the spoken duration of a text from its words and the language's speaking rate (words per minute, config host.words_per_minute); no speech has been made."""
    wpm = ai_config.host.words_per_minute
    return round(len(str(text or "").split()) / (wpm.get(language, wpm["en"]) / 60), 1)


def _units(narration: str, minimum: int = len(TEXT_PARTS)) -> list[list[str]]:
    """The narration as units of words: its sentences; while there are fewer than five, the longest unit is split at the clause end (comma, semicolon, dash) nearest its middle, else at its middle."""
    units = [u.split() for u in _SENTENCE_END.split(narration.strip()) if u.strip()]
    while len(units) < minimum:
        index = max(range(len(units)), key=lambda i: len(units[i]))
        words = units[index]
        if len(words) < 2:
            raise ValueError(f"a text needs at least {minimum} words")
        middle = len(words) / 2
        clauses = [i for i in range(1, len(words)) if _CLAUSE.search(words[i - 1])]
        cut = min(clauses, key=lambda i: abs(i - middle)) if clauses else round(middle)
        units[index:index + 1] = [words[:cut], words[cut:]]
    return units


def legacy_parts(narration: str, language: str = "en") -> dict[str, str]:
    """The five parts of a clip from its narration: the sentences (split further only when there are fewer than five) grouped in order so that the parts hold about 25, 12.5, 37.5, 15 and 10
    percent of the words (the proportions of the clip's segments); a part never starts in the middle of a sentence unless the narration has fewer than five. A narration of fewer than five
    words cannot be cut and is refused (ValueError)."""
    units = _units(str(narration or ""))
    total = sum(len(u) for u in units)
    cumulative = [sum(len(u) for u in units[:i + 1]) / total for i in range(len(units))]
    best = min(itertools.combinations(range(len(units) - 1), len(SHARES)), key=lambda cuts: sum(abs(cumulative[c] - share) for c, share in zip(cuts, SHARES)))
    edges = [0, *(c + 1 for c in best), len(units)]
    return {part: " ".join(" ".join(u) for u in units[a:b]) for part, a, b in zip(TEXT_PARTS, edges, edges[1:])}


def parts_of(hook: str, story: str, avatar: str) -> dict[str, str]:
    """The parts of a clip written as a hook, the interesting things and the avatar's closing line, under the keys the voice, review and video steps read: the hook, the whole story as ONE part
    (`context`, voiced in one request and one file, over the film's pictures) and the avatar's line (`reaction`, the avatar speaks it at the end). `detail` and `cta` stay empty: a part with no
    words is neither voiced nor shown."""
    return {"hook": hook, "context": story, "detail": "", "reaction": avatar, "cta": ""}


def spoken_seconds(clip: dict, language: str) -> float:
    """The estimated duration of a clip: of its narration, or of its parts when it only has those."""
    return narration_seconds(clip.get("narration") or " ".join(str(clip.get(part) or "") for part in TEXT_PARTS), language)


def with_estimate(clip: dict, language: str) -> dict:
    """The clip with its duration, honestly labelled: an ESTIMATE from the words (no speech has been made); `duration_of` gives the measured one once the clip has been voiced."""
    note = clip.get("selection") if isinstance(clip.get("selection"), dict) else {}
    return {**clip, "selection": {**note, "duration": {"seconds": spoken_seconds(clip, language), "kind": "estimate"}}}


def duration_of(row: ShortClip) -> dict:
    """{seconds, kind} of a stored clip: the real length of its synthesized voice when it has been voiced ("measured"), else the estimate from its words ("estimate")."""
    media = json.loads(row.media_json or "{}")
    if media.get("voice_seconds"):
        return {"seconds": media["voice_seconds"], "kind": "measured"}
    return {"seconds": spoken_seconds(json.loads(row.script_json), row.language), "kind": "estimate"}


def seconds_of(clip: dict, cfg: ShortClipsConfig | None = None) -> dict[str, float]:
    """The parts of a clip with their start second: the hook, the context, the detail, the reaction (with the invitation)."""
    cfg = cfg or ai_config.short_clips_settings()
    out, t = {}, 0.0
    for part in ("hook", "context", "detail", "reaction"):
        out[part] = round(t, 3)
        t += cfg.segments[part].seconds
    return out


def clips_of(db: Session, case_id: int, language: str, status: str | None = "valid") -> list[ShortClip]:
    """The newest version of every clip of a language (those that passed their checks, unless `status` is None), in the order of their keys."""
    rows = db.query(ShortClip).filter(ShortClip.case_id == case_id, ShortClip.language == language).order_by(ShortClip.id.desc()).all()
    newest: dict[str, ShortClip] = {}
    for r in rows:
        newest.setdefault(r.clip_key, r)
    return sorted((r for r in newest.values() if status is None or r.status == status), key=lambda r: r.clip_key)


def clip_dict(row: ShortClip) -> dict:
    return {"id": row.id, "case_id": row.case_id, "language": row.language, "clip_key": row.clip_key, "kind": row.kind, "status": row.status, "version": row.version,
            **json.loads(row.script_json or "{}"), "media": json.loads(row.media_json or "{}"), "validation": json.loads(row.validation_json or "{}")}

"""The semantic audio review of the Persian voice.

A model LISTENS to the audio of a voiced block (and later to the voice of the avatar video made from it) and judges it against the source text:
meaning, skipped or added phrases, dates, numbers, names, URLs, paths, technical terms, pronunciation and delivery. It runs after the existing
voice-repair loop (speech-to-text, pronunciation key, retakes), which stays as it is. A block that fails goes back to that loop with a
correction of what the voice is given (its text, a respelled word, an audio tag); the viewer's text never changes. After the allowed
corrections a block that still fails is marked failed and stops the film. Every block keeps a record of what was heard and decided."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt_sha256
from app.db.models import EditorialBlueprint, StoryVersion
from app.documentary import audio as A
from app.documentary.persian_numbers import NUMBER
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

ERROR_TYPES = {"meaning", "omission", "addition", "date", "number", "name", "url", "path", "technical_term", "pronunciation", "delivery"}
BLOCKING = {"critical", "major"}
TAG = re.compile(r"\[[^\[\]]*\]\s*")
URL = re.compile(r"https?://\S+|www\.\S+")
PATH = re.compile(r"(?<![\w/])(?:/[\w.\-]+){1,}/?")
SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+")
TASK = "Review the audio and answer with the JSON described in your instructions. The input follows.\n"


class SemanticReviewFailed(RuntimeError):
    """Blocks whose audio still fails the review after the allowed corrections (or a review that could not be made)."""

    def __init__(self, failed: list[dict]):
        self.failed = failed
        super().__init__("Semantic audio review failed for " + ", ".join(f"{f['block_id']} ({f['reason']})" for f in failed[:12])
                         + (f" and {len(failed) - 12} more" if len(failed) > 12 else ""))


def required(language: str) -> bool:
    return language in ai_config.semantic_review.languages


def protected_items(*texts: str) -> list[str]:
    """Dates, numbers, URLs and paths of the texts, which must come out exactly right."""
    found: list[str] = []
    for text in texts:
        for pattern in (URL, PATH, NUMBER):
            found += [m.group().rstrip(".,;:") for m in pattern.finditer(text or "")]
    return list(dict.fromkeys(found))


def normalise(raw: dict) -> dict:
    """The reviewer's answer in its fixed shape. A pass needs the model's pass, no critical or major error and enough confidence."""
    errors = []
    for e in raw.get("errors") or []:
        if isinstance(e, dict):
            errors.append({"type": e.get("type") if e.get("type") in ERROR_TYPES else "meaning",
                           "severity": e.get("severity") if e.get("severity") in {"critical", "major", "minor"} else "major",
                           "expected": str(e.get("expected") or ""), "heard": str(e.get("heard") or ""), "reason": str(e.get("reason") or "")})
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    passed = (raw.get("verdict") == "pass" and not any(e["severity"] in BLOCKING for e in errors)
              and confidence >= ai_config.semantic_review.min_confidence)
    rules = [{"surface": str(r["surface"]), "replacement": str(r["replacement"])} for r in raw.get("corrected_pronunciation_rules") or []
             if isinstance(r, dict) and r.get("surface") and r.get("replacement")]
    text, delivery = raw.get("corrected_text"), raw.get("corrected_delivery_instruction")
    return {"verdict": "pass" if passed else "fail", "confidence": confidence, "errors": errors,
            "corrected_text": text if isinstance(text, str) and text.strip() else None, "corrected_pronunciation_rules": rules,
            "corrected_delivery_instruction": delivery if isinstance(delivery, str) and delivery in ai_config.semantic_review.delivery_tags else None}


def whole_word(surface: str) -> re.Pattern:
    return re.compile(rf"(?<![\w‌]){re.escape(surface)}(?![\w‌])")


def corrected_block(block: dict, review: dict) -> tuple[dict, list[str]]:
    """The block with the reviewer's corrections applied to what the voice is given (the sentences' tts forms), and what was applied. The
    sentences' speech and display text, which people read, are never touched. A corrected text is taken only when it has the same
    sentences as the block."""
    sentences = [dict(s) for s in block.get("sentences") or []]
    applied: list[str] = []
    forms = [s.get("tts") or s["speech"] for s in sentences]
    text = review.get("corrected_text")
    if text and sentences:
        parts = [p for p in SENTENCE_END.split(text.strip()) if p]
        if len(parts) == len(forms):
            forms = parts
            applied.append("text")
        else:
            applied.append("text ignored: its sentences are not those of the block")
    for rule in review.get("corrected_pronunciation_rules") or []:
        pattern = whole_word(rule["surface"])
        if any(pattern.search(f) for f in forms):
            forms = [pattern.sub(rule["replacement"], f) for f in forms]
            applied.append(f"respelled {rule['surface']}")
    tag = review.get("corrected_delivery_instruction")
    if tag and forms and not forms[0].startswith(tag):
        forms[0] = f"{tag} {forms[0]}"
        applied.append(f"tag {tag}")
    if not sentences:
        return block, []
    out = {**block, "sentences": [{**s, "tts": f} for s, f in zip(sentences, forms)]}
    out["tts_text"] = " ".join(forms)
    return out, [a for a in applied if not a.startswith("text ignored")] + [a for a in applied if a.startswith("text ignored")]


def beats_of(block: dict) -> list[str]:
    """The beats of the story a block tells (a block of "B08.g02" tells beat B08)."""
    found = [b["beat_id"] for b in block.get("beats") or [] if b.get("beat_id")]
    return list(dict.fromkeys(found or [(block.get("section_id") or "").split(".")[0]]))


def english_sources(db: Session, version: StoryVersion) -> dict[str, str]:
    """For each beat of this version, the English film's text of the same stretch of the story (the beats of two films are cut differently:
    they are matched by their place in the story). Empty when the case has no English film to compare with."""
    from app.documentary.blueprint import version_sections
    from app.documentary.mvp import reference as REF
    from app.documentary.spoken import beat_sections

    struct = json.loads(version.narrative_structure or "{}")
    mine = db.get(EditorialBlueprint, struct.get("blueprint_id") or -1)
    english_plan = REF.english_needs_plan(db, version.case_id)
    theirs = db.get(EditorialBlueprint, english_plan.blueprint_id) if english_plan is not None else None
    master = db.get(StoryVersion, theirs.story_version_id) if theirs is not None else None
    if mine is None or theirs is None or master is None:
        return {}
    english_text = {s["id"]: s["text"] for s in beat_sections(version_sections(master), json.loads(theirs.blueprint_json or "{}"))}
    english_spans = REF.beat_spans(theirs.blueprint_json)
    limit = ai_config.semantic_review.source_chars
    order = list(english_spans)

    def window(span: tuple[float, float]) -> list[str]:
        """The English beats of this stretch and the one before and after (the two films cut their beats at other points)."""
        hit = [order.index(b) for b in REF.matching_beats(span, english_spans)]
        if not hit:
            return []
        return order[max(min(hit) - 1, 0):min(max(hit) + 2, len(order))]

    return {beat: " ".join(english_text.get(b, "") for b in window(span))[:limit] for beat, span in REF.beat_spans(mine.blueprint_json).items()}


def tones_of(db: Session, version: StoryVersion) -> dict[str, dict]:
    struct = json.loads(version.narrative_structure or "{}")
    row = db.get(EditorialBlueprint, struct.get("blueprint_id") or -1)
    beats = json.loads(row.blueprint_json or "{}").get("beats") or [] if row else []
    return {b["id"]: {k: b.get(k) for k in ("purpose", "emotional_load") if b.get(k)} for b in beats}


class Reviewer:
    """Listens to audio for the semantic review and keeps the record of every block."""

    def __init__(self, db: Session, case_id: int, sources: dict[str, str] | None = None, tones: dict[str, dict] | None = None,
                 runner: Callable[[object, str, list[str]], Awaitable[tuple[dict, object]]] | None = None):
        self.db, self.case_id = db, case_id
        self.sources, self.tones = sources or {}, tones or {}
        self.runner = runner or (lambda gen, body, audio: run_agent("documentary.semantic_review.audio", gen, TASK + body, images=audio))
        self.records: dict[str, dict] = {}

    def context(self, block: dict) -> dict:
        cfg = ai_config.semantic_review
        sentences = block.get("sentences") or []
        display = " ".join(s.get("display") or s["speech"] for s in sentences) or block.get("display_text") or block["text"]
        tts = block.get("tts_text") or " ".join(s.get("tts") or s["speech"] for s in sentences) or block["text"]
        beats = beats_of(block)                    # the sources and the tones are of the story's beats, not of the block's section ("B08.g02" tells beat B08)
        source = "\n\n".join(self.sources[beat] for beat in beats if self.sources.get(beat))
        return {"source_text": (block.get("source_text") or source)[:cfg.source_chars],
                "scene_context": next((self.tones[beat] for beat in beats if self.tones.get(beat)), None) or block.get("scene_context") or {},
                "display_text": display, "tts_text": tts, "protected_items": protected_items(display, tts),
                "previous_text": (block.get("previous_text") or "")[-cfg.neighbour_chars:] if cfg.neighbour_chars else "",
                "next_text": (block.get("next_text") or "")[:cfg.neighbour_chars], "allowed_delivery_tags": cfg.delivery_tags}

    async def _ask(self, runner, body: str, data: str, label: str) -> dict:
        """One model call on one audio clip, made again when the provider hiccups; the block is not judged when it keeps failing."""
        cfg = ai_config.semantic_review
        for attempt in range(cfg.call_retries + 1):
            try:
                with track_run(self.db, self.case_id, f"semantic review {label}", input_summary=label) as run:
                    got, res = await runner(get_generation_provider(), body, [data])
                    stamp_run(run, res, "semantic_audio_reviewer")
                return got if isinstance(got, dict) else {}
            except GenerationError as e:
                if attempt == cfg.call_retries:
                    raise SemanticReviewFailed([{"block_id": label, "reason": f"the review could not be made: {type(e).__name__}: {e}"[:200]}]) from e
                await asyncio.sleep(cfg.retry_pause_s)
        return {}

    async def listen(self, context: dict, audio: Path, label: str) -> dict:
        """One review of one audio file against its context."""
        mp3 = audio.with_suffix(".review.mp3")
        if not mp3.exists():
            await asyncio.to_thread(A.to_review_mp3, audio, mp3)
        body = json.dumps(context, ensure_ascii=False)
        saved = audio.with_suffix(".review.json")          # the same audio, context and instructions are never judged twice (a resumed run)
        key = hashlib.sha256(mp3.read_bytes() + body.encode() + prompt_sha256("documentary/semantic_review/system").encode()).hexdigest()
        try:
            cached = json.loads(saved.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                return cached["result"]
        except (OSError, ValueError, KeyError):
            pass
        data = "data:audio/mpeg;base64," + base64.b64encode(mp3.read_bytes()).decode()
        result = normalise(await self._ask(self.runner, body, data, label))
        saved.write_text(json.dumps({"key": key, "result": result}, ensure_ascii=False), encoding="utf-8")
        return result

    async def review_block(self, result: dict) -> dict:
        """The review of a voiced block: the whole block, listened to once against its context."""
        return await self.listen(self.context(result["block"]), Path(result["take"]["wav_path"]), result["block"]["block_id"])

    async def run(self, result: dict, speak: Callable[[dict, int], Awaitable[dict]], seed: int) -> dict:
        """The review of a voiced block, with the corrections: `result` is what the voice-repair loop made, `speak(block, seed)` runs that loop
        again for a corrected block. Returns the result that is kept; its record says whether it was approved."""
        cfg = ai_config.semantic_review
        block = result["block"]
        record = {"block_id": block["block_id"], "voice_attempts": 1, "semantic_review_attempts": 0, "voice_review_result": "fail",
                  "avatar_review_result": "not_applicable", "errors": [], "final_status": "failed", "reviews": []}
        self.records[block["block_id"]] = record
        for attempt in range(cfg.max_corrections + 1):
            try:
                review = await self.review_block(result)
            except SemanticReviewFailed as e:           # this block could not be reviewed: it fails alone, the others go on
                record["failure"] = e.failed[0]["reason"]
                break
            record["semantic_review_attempts"] += 1
            record["reviews"].append({"attempt": attempt + 1, **review})
            record["errors"] = review["errors"]
            if review["verdict"] == "pass":
                record["voice_review_result"], record["final_status"] = "pass", "approved"
                break
            if attempt == cfg.max_corrections:
                record["failure"] = "still failing after the allowed corrections"
                break
            fixed, applied = corrected_block(result["block"], review)
            record["reviews"][-1]["applied"] = applied
            seed += 1                      # a correction that changes nothing in the request is still a new take
            result = await speak(fixed, seed)
            record["voice_attempts"] += 1
        result["semantic_review"] = record
        return result


def save_records(out: Path, records: dict[str, dict]) -> Path:
    path = out / "semantic_review.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def failed_blocks(records: dict[str, dict]) -> list[dict]:
    return [{"block_id": r["block_id"], "reason": r.get("failure") or "failed"} for r in records.values() if r["final_status"] != "approved"]

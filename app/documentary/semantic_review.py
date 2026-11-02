"""The semantic audio review of the Persian voice.

A model LISTENS to the audio of a voiced block (and later to the voice of the avatar video made from it) and judges it against the source text:
meaning, skipped or added phrases, dates, numbers, names, URLs, paths, technical terms, pronunciation and delivery. It runs after the existing
voice-repair loop (speech-to-text, pronunciation key, retakes), which stays as it is. A block that fails goes back to that loop with a
correction of what the voice is given (its text, a respelled word, an audio tag); the viewer's text never changes. After the allowed
corrections a block that still fails is marked failed and stops the film. Every block keeps a record of what was heard and decided."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.documentary import audio as A
from app.documentary.persian_numbers import NUMBER
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

ERROR_TYPES = {"meaning", "omission", "addition", "date", "number", "name", "url", "path", "technical_term", "pronunciation", "delivery"}
BLOCKING = {"critical", "major"}
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


class Reviewer:
    """Listens to audio for the semantic review and keeps the record of every block."""

    def __init__(self, db: Session, case_id: int, sources: dict[str, str] | None = None, tones: dict[str, dict] | None = None,
                 rules: list[dict] | None = None, runner: Callable[[object, str, list[str]], Awaitable[tuple[dict, object]]] | None = None):
        self.db, self.case_id = db, case_id
        self.sources, self.tones, self.rules = sources or {}, tones or {}, rules or []
        self.runner = runner or (lambda gen, body, audio: run_agent("documentary.semantic_review.audio", gen, TASK + body, images=audio))
        self.records: dict[str, dict] = {}

    def context(self, block: dict) -> dict:
        cfg = ai_config.semantic_review
        sentences = block.get("sentences") or []
        display = " ".join(s.get("display") or s["speech"] for s in sentences) or block.get("display_text") or block["text"]
        tts = block.get("tts_text") or " ".join(s.get("tts") or s["speech"] for s in sentences) or block["text"]
        section = block.get("section_id")
        return {"source_text": (block.get("source_text") or self.sources.get(section, ""))[:cfg.source_chars],
                "scene_context": self.tones.get(section) or block.get("scene_context") or {},
                "display_text": display, "tts_text": tts, "protected_items": protected_items(display, tts),
                "pronunciation_rules": self.rules, "previous_text": (block.get("previous_text") or "")[-cfg.neighbour_chars:] if cfg.neighbour_chars else "",
                "next_text": (block.get("next_text") or "")[:cfg.neighbour_chars], "allowed_delivery_tags": cfg.delivery_tags}

    async def listen(self, context: dict, audio: Path, label: str) -> dict:
        """One review of one audio file against its context."""
        mp3 = audio.with_suffix(".review.mp3")
        if not mp3.exists():
            await asyncio.to_thread(A.to_review_mp3, audio, mp3)
        data = "data:audio/mpeg;base64," + base64.b64encode(mp3.read_bytes()).decode()
        body = json.dumps(context, ensure_ascii=False)
        try:
            with track_run(self.db, self.case_id, f"semantic review {label}", input_summary=label) as run:
                got, res = await self.runner(get_generation_provider(), body, [data])
                stamp_run(run, res, "semantic_audio_reviewer")
        except GenerationError as e:
            raise SemanticReviewFailed([{"block_id": label, "reason": f"the review could not be made: {type(e).__name__}: {e}"[:200]}]) from e
        return normalise(got if isinstance(got, dict) else {})

    async def run(self, result: dict, speak: Callable[[dict, int], Awaitable[dict]], seed: int) -> dict:
        """The review of a voiced block, with the corrections: `result` is what the voice-repair loop made, `speak(block, seed)` runs that loop
        again for a corrected block. Returns the result that is kept; its record says whether it was approved."""
        cfg = ai_config.semantic_review
        block = result["block"]
        record = {"block_id": block["block_id"], "voice_attempts": 1, "semantic_review_attempts": 0, "voice_review_result": "fail",
                  "avatar_review_result": "not_applicable", "errors": [], "final_status": "failed", "reviews": []}
        self.records[block["block_id"]] = record
        for attempt in range(cfg.max_corrections + 1):
            review = await self.listen(self.context(result["block"]), Path(result["take"]["wav_path"]), block["block_id"])
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

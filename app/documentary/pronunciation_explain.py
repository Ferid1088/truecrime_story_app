"""Why a word of a narration is (or is not) changed for the voice: the planner run dry over a version's stored readings, word by word, with everything
behind its decision. It calls no model and no voice and writes nothing; the memory is only read.

For every word it shows: the original word and its place in the text, the sentence, the Finglish reading, the phonemes (IPA), the syllables and the
rule-based stress, the morphology, the complete REFERENCE_IRAB and what its validator found, the ezafe, the risk with its evidence, the planner's
decision with its basis, the exact text the voice is given for the word, the memory's trust level, and where the block is in the narration."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    PronunciationEntry,
    PronunciationIntervention,
    ReadingApproval,
    StoryVersion,
)
from app.documentary import voice_prepare as VP
from app.documentary.performance import performance_for_version
from app.documentary.pronunciation_memory import trust_level
from app.documentary.pronunciation_planner import CHANGES, Planner
from app.documentary.reading import answer as ANS
from app.documentary.reading import consistency as CS
from app.documentary.reading import stage as RS
from app.documentary.reading.answer import ReadingWord
from app.documentary.reading.block import block_inputs
from app.documentary.reading.table import FilmTable
from app.documentary.reading.voicing import Voicing, WordSignal
from app.documentary.reading.words import key_of, tokenize


def load(db: Session, version_id: int, language: str) -> tuple[StoryVersion, dict, Voicing, frozenset[str]]:
    """(the version, its performance plan, the voicing of its stored readings, the words the film dictionary covers). Raises OSError when the version has
    no stored reading for the language."""
    version = db.get(StoryVersion, version_id)
    base = Path(ai_config.voice.work_dir)
    if not base.is_absolute():
        base = Path(__file__).resolve().parents[2] / base
    cache = json.loads((base / str(version.case_id) / ai_config.reading.folder / f"{language}_v{version.id}.json").read_text(encoding="utf-8"))
    plan = performance_for_version(db, version)
    items = block_inputs(plan["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
    approvals = [{"surface": a.surface, "latin": a.latin, "kind": a.kind} for a in db.query(ReadingApproval).filter(ReadingApproval.language == language)]
    table = FilmTable.build(cache["reader"], approvals)
    readings, signals, words, stored = {}, {}, {}, {}
    for item in items:
        saved = cache["blocks"].get(item.block_id)
        if not saved or len(saved["readings"]) != len(item.words):
            continue
        doubted = {d["n"] for d in saved["doubts"]}
        # a block read before the film table changed holds the old table's readings: the current table is applied as the reading stage applies it
        current, notes = ANS.enforce([ReadingWord(**r) for r in saved["readings"]], item.words, table, ai_config.reading.pause_between_names)
        overridden = {int(m.group(1)) for note in saved["notes"] + notes if (m := re.match(r"word (\d+)", note))}
        readings[item.block_id] = [r.latin for r in current]
        signals[item.block_id] = [WordSignal(r.confidence, r.n in doubted, r.n in overridden) for r in current]
        words[item.block_id] = list(zip(item.words, readings[item.block_id], strict=True))
        stored[item.block_id] = current
    names = RS.names_of(table)
    voicing = Voicing({key_of(a["surface"]): a["latin"] for a in approvals}, readings, signals, names, RS.opinions_of(stored), CS.inconsistent(CS.variants(words, names)))
    covered = frozenset(key_of(r.get("string_to_replace") or "") for r in VP.sent_rules(version))
    return version, plan, voicing, covered


def memory_of(db: Session, language: str, keys: set[str]) -> dict[str, list[dict]]:
    """The memory's entries of these words with their trust level and their interventions (the correction history)."""
    out: dict[str, list[dict]] = {}
    for e in db.query(PronunciationEntry).filter(PronunciationEntry.language == language, PronunciationEntry.surface_key.in_(keys)):
        tries = db.query(PronunciationIntervention).filter_by(entry_id=e.id).all()
        out.setdefault(e.surface_key, []).append({
            "id": e.id, "surface": e.surface, "status": e.status, "trust": trust_level(e, tries), "next_word": e.next_word, "sense": e.sense,
            "approved_pronunciation": e.approved_pronunciation,
            "interventions": [{"kind": i.kind, "replacement": i.replacement, "status": i.status, "successes": i.successes, "failures": i.failures} for i in tries]})
    return out


def block_times(version: StoryVersion, language: str) -> dict[str, tuple[float, float]]:
    """Where each block is in the version's narration of the language (empty when it has none)."""
    from app.documentary.voice_render import VoiceRenderer

    try:
        manifest = json.loads((VoiceRenderer.out_dir_for(version.case_id, language, version.id) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {b["block_id"]: (b["start"], b["end"]) for b in manifest.get("timeline", {}).get("blocks", [])}


def explain(db: Session, version_id: int, language: str, scope: str = "risky") -> dict:
    """Every word worth explaining (scope "risky": HIGH or CRITICAL, or changed, or escalated; "all": every word) with everything behind its decision."""
    version, plan, voicing, covered = load(db, version_id, language)
    lang = ai_config.voice.for_language(language)
    planner = Planner(db, language, lang.voice_id, lang.model_id)
    times = block_times(version, language)
    rows: list[dict] = []
    counts = {"words": 0, "LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "changed": 0, "escalated": 0, "reference_valid": 0, "reference_checked": 0}
    for block in plan["blocks"]:
        if block["block_id"] not in voicing.readings:
            continue
        text = block.get("tts_text") or block["text"]
        bp = planner.plan(block["block_id"], text, voicing, covered=covered)
        words = tokenize(text)
        sentence = " ".join(w.surface + w.after for w in words)
        for w, r, d, b in zip(words, bp.risks, bp.decisions, bp.boundaries, strict=True):
            counts["words"] += 1
            counts[r.level] += 1
            counts["changed"] += d.strategy in CHANGES
            counts["escalated"] += d.strategy == "escalate"
            if d.reference is not None:
                counts["reference_checked"] += 1
                counts["reference_valid"] += d.reference.valid
            if scope != "all" and r.level not in ("HIGH", "CRITICAL") and d.strategy == "keep":
                continue
            rows.append({"block_id": block["block_id"], "n": w.n, "word": w.surface, "start": w.start, "end": w.end, "sentence": sentence,
                         "finglish": voicing.readings[block["block_id"]][w.n], "reference": d.reference.describe() if d.reference else None,
                         "ezafe": b.describe() if b.ezafe_type != "NONE" else None, "risk": r.describe(), "decision": d.describe(),
                         "tts_input": d.replacement if d.strategy in CHANGES and d.replacement else w.surface,
                         "audio": dict(zip(("start", "end"), times[block["block_id"]], strict=True)) if block["block_id"] in times else None})
    memory = memory_of(db, language, {key_of(row["word"]) for row in rows})
    for row in rows:
        row["memory"] = memory.get(key_of(row["word"]), [])
        row["verification"] = next((m["trust"] for m in row["memory"] if m["trust"] in ("OWNER_APPROVED_READING", "ACOUSTICALLY_VERIFIED")), "UNVERIFIED")
    texts = {b["block_id"]: b.get("tts_text") or b["text"] for b in plan["blocks"] if b["block_id"] in voicing.readings}
    found = CS.variants({b: list(zip(tokenize(t), voicing.readings[b], strict=True)) for b, t in texts.items()}, voicing.names)
    return {"version_id": version.id, "case_id": version.case_id, "language": language, "voice_id": lang.voice_id, "model_id": lang.model_id,
            "consistency": [v.describe() for v in found],
            "audio_url": f"/api/documentary/versions/{version.id}/audio/narration" if times else None, "counts": counts, "words": rows}

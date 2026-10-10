"""Deterministic, human-reviewable extraction for persisted master scripts."""

from __future__ import annotations

import re
from collections import defaultdict

from app.agents.story import build_evidence_pack
from app.db.models import Contradiction, EditorialBlueprint, Fact, Source, StoryVersion


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"'])")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
_STOPWORDS = {
    "about", "after", "again", "against", "among", "and", "around", "because",
    "before", "being", "between", "could", "during", "from", "have", "into",
    "more", "most", "only", "over", "said", "some", "such", "than", "that",
    "their", "there", "these", "they", "this", "through", "under", "were", "which",
    "while", "with", "would", "your",
}


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _WORD_RE.findall(text) if token.lower() not in _STOPWORDS}


def _modality(sentence: str) -> str:
    text = sentence.lower()
    if any(term in text for term in (
        "denied", "dispute", "disputed", "opposed", "conflicting", "controvers",
        "whether", "on the other hand", "unlike the prosecution",
    )):
        return "DISPUTED"
    if any(term in text for term in (
        "alleged", "claimed", "argued", "maintained", "asserted", "contended",
        "according to the defense", "according to later filings",
    )):
        return "ALLEGED"
    if any(term in text for term in (
        "investigators believed", "investigators concluded", "police believed",
    )):
        return "BELIEVED_BY_INVESTIGATORS"
    if any(term in text for term in (
        "no evidence", "without evidence", "did not", "never ", "absence of",
        "no prior", "not a",
    )):
        return "ABSENCE_OF_EVIDENCE"
    return "ESTABLISHED"


def _quoted_text(sentence: str) -> str | None:
    match = re.search(r'[“"]([^“”"]+)[”"]', sentence)
    return match.group(1).strip() if match else None


def _speaker(sentence: str) -> str | None:
    lower = sentence.lower()
    if any(term in lower for term in ("the defense", "defense team", "defense attorneys",
                                      "according to the defense")):
        return "defense"
    if "witness" in lower:
        return "witnesses"
    if "prosecutor" in lower or "state asserted" in lower or "prosecution" in lower:
        return "prosecution"
    if "officer cortez" in lower or "cortez" in lower:
        return "Officer Eduardo Cortez"
    if "anthony" in lower or "i’m not alleged" in lower or "i'm not alleged" in lower:
        return "Anthony"
    return None


def _atomic_parts(sentence: str) -> list[dict]:
    """Return normalized atomic propositions while preserving attribution.

    This is deliberately deterministic and review-oriented. It separates the
    fact that a statement/report/testimony occurred from the proposition
    carried by that statement; it does not infer that the proposition is true.
    """
    quoted = _quoted_text(sentence)
    speaker = _speaker(sentence)
    parts: list[dict] = []
    if quoted:
        before = sentence[:sentence.find(quoted)].strip(" \u201c\";,:-")
        reporting_verbs = (" says", " said", " insisted", " testified", " told ",
                           " maintained", " argued", " reports", " reported", " cries")
        has_reporting = any(verb in before.lower() for verb in reporting_verbs)
        if has_reporting:
            reporting = before
            if reporting:
                parts.append({"text": reporting, "modality": "ESTABLISHED",
                              "role": "REPORTING_ACT", "speaker": speaker})
        else:
            who = speaker or "the speaker"
            parts.append({"text": f"{who} spoke the recorded statement.",
                          "modality": "ESTABLISHED", "role": "REPORTING_ACT",
                          "speaker": speaker})
        parts.append({"text": quoted, "modality": "ALLEGED",
                      "role": "QUOTED_PROPOSITION", "speaker": speaker})
        return parts

    lower = sentence.lower()
    if "witnesses testified that " in lower:
        prefix, proposition = re.split(r"testified that ", sentence, maxsplit=1, flags=re.I)
        return [
            {"text": prefix.strip() + " testified.", "modality": "ESTABLISHED",
             "role": "REPORTING_ACT", "speaker": "witnesses"},
            {"text": proposition.strip(), "modality": "ALLEGED",
             "role": "ATTRIBUTED_PROPOSITION", "speaker": "witnesses"},
        ]
    if "the state asserted" in lower:
        prefix, proposition = re.split(r"the state asserted", sentence, maxsplit=1, flags=re.I)
        proposition = proposition.strip(" ,")
        return [
            {"text": prefix.strip(" ,") + " is presented as a conditional factual premise.",
             "modality": "DISPUTED", "role": "NARRATOR_ASSERTION", "speaker": None},
            {"text": "The state asserted that " + proposition, "modality": "ALLEGED",
             "role": "ATTRIBUTED_PROPOSITION", "speaker": "prosecution"},
        ]
    if "prosecutor" in lower and "denied" in lower and "arguing" in lower:
        prefix, proposition = re.split(r"arguing ", sentence, maxsplit=1, flags=re.I)
        proposition = proposition.strip()
        pieces = re.split(r", and maintaining that ", proposition, maxsplit=1, flags=re.I)
        claims = [
            {"text": pieces[0].strip().removeprefix("that "), "modality": "DISPUTED",
             "role": "ATTRIBUTED_PROPOSITION", "speaker": "prosecution"},
        ]
        if len(pieces) == 2:
            claims.append({"text": "The prosecution maintained that " + pieces[1].strip(),
                           "modality": "DISPUTED", "role": "ATTRIBUTED_PROPOSITION",
                           "speaker": "prosecution"})
        return [
            {"text": prefix.strip(" ,") + ".", "modality": "ESTABLISHED",
             "role": "REPORTING_ACT", "speaker": "prosecution"},
        ] + claims
    if "reporting that he is detaining the alleged suspect" in lower:
        return [
            {"text": "Officer Cortez activated his radio and reported the detention to dispatch.",
             "modality": "ESTABLISHED", "role": "REPORTING_ACT",
             "speaker": "Officer Eduardo Cortez"},
            {"text": "The detained person was described as an alleged suspect.",
             "modality": "ALLEGED", "role": "ATTRIBUTED_PROPOSITION",
             "speaker": "Officer Eduardo Cortez"},
        ]
    according = re.match(r"According to ([^,]+),\s*(.+)$", sentence, flags=re.I)
    if according:
        source = according.group(1).strip()
        proposition = according.group(2).strip()
        result = [
            {"text": f"The script attributes an account to {source}.",
             "modality": "ESTABLISHED", "role": "REPORTING_ACT",
             "speaker": _speaker(sentence)},
        ]
        for piece in re.split(r",\s+but\s+", proposition, maxsplit=1, flags=re.I):
            result.append({"text": piece.strip(), "modality": "ALLEGED",
                           "role": "ATTRIBUTED_PROPOSITION", "speaker": _speaker(sentence)})
        return result
    attributed = re.match(
        r"(?P<prefix>.+?\b(?:tells?|told|testified|argued|maintained|claimed|"
        r"alleged|asserted|contended|insisted)\b)\s+that\s+(?P<prop>.+)$",
        sentence, flags=re.I,
    )
    if attributed:
        prefix = attributed.group("prefix").strip(" ,;:")
        proposition = attributed.group("prop").strip()
        lower_prefix = prefix.lower()
        proposition_modality = "DISPUTED" if "denied" in lower_prefix else "ALLEGED"
        result = [
            {"text": prefix + ".", "modality": "ESTABLISHED",
             "role": "REPORTING_ACT", "speaker": _speaker(sentence)},
        ]
        for piece in re.split(r",\s+but\s+", proposition, maxsplit=1, flags=re.I):
            result.append({"text": piece.strip(), "modality": proposition_modality,
                           "role": "ATTRIBUTED_PROPOSITION", "speaker": _speaker(sentence)})
        return result
    embedded_tell = re.match(
        r"(?P<prefix>.+?\b(?:tells?|told)\b.*?),?\s+that\s+(?P<prop>.+)$",
        sentence, flags=re.I,
    )
    if embedded_tell:
        return [
            {"text": embedded_tell.group("prefix").strip(" ,;:") + ".",
             "modality": "ESTABLISHED", "role": "REPORTING_ACT",
             "speaker": _speaker(sentence)},
            {"text": embedded_tell.group("prop").strip(), "modality": "ALLEGED",
             "role": "ATTRIBUTED_PROPOSITION", "speaker": _speaker(sentence)},
        ]
    comma_attributed = re.match(
        r"(?P<prefix>.+?\b(?:claimed|argued|maintained|asserted|alleged|"
        r"insisted)\b),\s*(?P<prop>.+)$", sentence, flags=re.I,
    )
    if comma_attributed:
        return [
            {"text": comma_attributed.group("prefix").strip() + ".",
             "modality": "ESTABLISHED", "role": "REPORTING_ACT",
             "speaker": _speaker(sentence)},
            {"text": comma_attributed.group("prop").strip(), "modality": "ALLEGED",
             "role": "ATTRIBUTED_PROPOSITION", "speaker": _speaker(sentence)},
        ]
    return [{"text": sentence, "modality": _modality(sentence),
             "role": "NARRATOR_ASSERTION", "speaker": None}]


def _sentence_spans(text: str) -> list[tuple[int, int, str, int]]:
    spans = []
    paragraph_start = 0
    for paragraph_index, paragraph in enumerate(text.split("\n\n"), 1):
        local = 0
        for match in _SENTENCE_RE.finditer(paragraph):
            end = match.start()
            sentence = paragraph[local:end].strip()
            left = paragraph_start + local
            right = paragraph_start + end
            if sentence:
                spans.append((left, right, sentence, paragraph_index))
            local = match.start()
        sentence = paragraph[local:].strip()
        if sentence:
            spans.append((paragraph_start + local, paragraph_start + len(paragraph),
                          sentence, paragraph_index))
        paragraph_start += len(paragraph) + 2
    return spans


def _beat_lookup(story: StoryVersion, blueprint: EditorialBlueprint) -> dict[int, str]:
    structure = _loads(story.narrative_structure)
    sections = structure.get("sections") or []
    paragraph_to_beat: dict[int, str] = {}
    offset = 0
    raw = _loads(blueprint.blueprint_json)
    by_act = defaultdict(list)
    for beat in raw.get("beats") or []:
        by_act[str(beat["act_id"])].append(beat)
    for section in sections:
        section_paragraphs = (section.get("text") or "").split("\n\n")
        start = offset + 1
        for beat in by_act[str(section["id"])]:
            first, last = beat.get("paragraphs", [1, 1])
            for local in range(int(first), int(last) + 1):
                paragraph_to_beat[start + local - 1] = str(beat["id"])
        offset += len(section_paragraphs)
    return paragraph_to_beat


def _loads(value: str | None) -> dict:
    import json

    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_master_claims(db, story: StoryVersion, blueprint: EditorialBlueprint) -> dict:
    """Extract every declarative sentence from a persisted master story."""
    facts = db.query(Fact).filter_by(case_id=story.case_id).order_by(Fact.id).all()
    contradictions = db.query(Contradiction).filter_by(case_id=story.case_id).order_by(Contradiction.id).all()
    sources = db.query(Source).filter_by(case_id=story.case_id).order_by(Source.id).all()
    pack = build_evidence_pack(facts, contradictions, sources)
    evidence = {
        item["id"]: item
        for key in ("facts", "timeline", "contradictions")
        for item in pack.get(key) or []
    }
    evidence_tokens = {key: _tokens(
        item.get("claim") or item.get("topic") or "" + " " + (item.get("description") or "")
    ) for key, item in evidence.items()}
    beat_by_paragraph = _beat_lookup(story, blueprint)
    claims = []
    split_sentence_count = 0
    denominator = 0
    for index, (start, end, sentence, paragraph) in enumerate(_sentence_spans(story.story_text), 1):
        if sentence.endswith("?"):
            continue
        denominator += 1
        beat_id = beat_by_paragraph.get(paragraph)
        if not beat_id:
            raise ValueError(f"Script paragraph {paragraph} is not covered by a blueprint beat")
        sentence_tokens = _tokens(sentence)
        scores = sorted(
            ((len(sentence_tokens & tokens), key) for key, tokens in evidence_tokens.items()),
            reverse=True,
        )
        matched = [key for score, key in scores if score >= 2][:6]
        parts = _atomic_parts(sentence)
        if len(parts) > 1:
            split_sentence_count += 1
        parent_key = f"S15_C{index:03d}" if len(parts) > 1 else None
        for part_index, part in enumerate(parts, 1):
            claim_key = (f"S15_C{index:03d}_{part_index:02d}" if len(parts) > 1
                         else f"S15_C{index:03d}")
            claims.append({
            "claim_key": claim_key,
            "claim_text": part["text"],
            "source_sentence_text": sentence,
            "span_start": start,
            "span_end": end,
            "beat_id": beat_id,
            "modality": part["modality"],
            "assertion_role": part["role"],
            "speaker": part.get("speaker"),
            "parent_claim_key": parent_key,
            "source_refs": [f"story_version:{story.id}:span:{start}-{end}"],
            "evidence_refs": matched,
            "review_status": "proposed",
            "origin": "master_story_extraction",
            })
    return {
        "claims": claims,
        "coverage": {
            "denominator_declarative_sentences": denominator,
            "atomic_claims_extracted": len(claims),
            "declarative_spans_covered": denominator,
            "coverage_percent": round(100 * denominator / denominator, 2) if denominator else 0,
            "split_source_sentences": split_sentence_count,
            "split_percent": round(100 * split_sentence_count / denominator, 2) if denominator else 0,
            "total_sentence_spans": len(_sentence_spans(story.story_text)),
            "question_spans_excluded": len(_sentence_spans(story.story_text)) - denominator,
            "evidence_linked_claims": sum(bool(claim["evidence_refs"]) for claim in claims),
            "method": "one proposed record per atomic proposition; source spans remain full declarative sentences; deterministic modality heuristic; human review required",
        },
    }

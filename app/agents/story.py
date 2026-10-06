import copy
import hashlib
import json
import re
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session
from rapidfuzz import fuzz
from app.core.ai_config import ai_config
from app.db.models import (
    AgentRun, Case, Fact, Contradiction, Source, StoryVersion,
)
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationResult
from app.services.tracking import track_run, stamp_run
from app.utils import (
    language_quality, strip_narration_artifacts, text_hash, utc_now,
)


# ---------------------------------------------------------------------------
# Evidence pack: the ONLY material the writer is allowed to narrate from.
# Internal IDs (F001, T001, C001, CTX001) never appear in story output.
# ---------------------------------------------------------------------------

def _by_id(rows: list) -> list:
    """Deterministic evidence order: database primary key first, insertion
    order for rows that are not flushed yet (id None). Without this, the
    positional IDs below depend on whatever order the query returned."""
    return sorted(
        rows,
        key=lambda r: (getattr(r, "id", None) is None, getattr(r, "id", None) or 0),
    )


def evidence_fingerprint(pack: dict) -> str:
    """Short hash of what every evidence ID *means* in this pack.

    Pack IDs are positional (F001 = first fact of the case), and research
    re-runs replace the whole fact set. A story stores this fingerprint
    so later stages (localization, voice, visuals) can tell whether the
    F012 in its plan still refers to the same fact — instead of silently
    resolving it against a different one.
    """
    payload = {
        "facts": [
            [f["id"], f.get("claim"), bool(f.get("uncertain"))]
            for f in pack.get("facts") or []
        ],
        "timeline": [
            [t["id"], t.get("event_date"), t.get("claim")]
            for t in pack.get("timeline") or []
        ],
        "contradictions": [
            [c["id"], c.get("topic"), c.get("description")]
            for c in pack.get("contradictions") or []
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_evidence_pack(
    facts: list[Fact], contradictions: list[Contradiction], sources: list[Source]
) -> dict:
    q = ai_config.story_quality
    facts = _by_id(list(facts))
    contradictions = _by_id(list(contradictions))
    sources = _by_id(list(sources))
    fact_items = []
    for i, f in enumerate(facts, 1):
        uncertain = bool(f.disputed) or f.confidence < q.disputed_confidence_threshold
        try:
            source_ids = json.loads(f.source_ids_json or "[]")
        except (ValueError, TypeError):
            source_ids = []
        try:
            chunk_ids = json.loads(f.chunk_ids_json or "[]")
        except (ValueError, TypeError):
            chunk_ids = []
        try:
            people = json.loads(f.people_json or "[]")
        except (ValueError, TypeError):
            people = []
        try:
            locations = json.loads(f.locations_json or "[]")
        except (ValueError, TypeError):
            locations = []
        fact_items.append(
            {
                "id": f"F{i:03d}",
                "claim": f.claim,
                "original_claim": f.original_claim,
                "original_language": f.original_language,
                "category": f.category,
                "confidence": f.confidence,
                "disputed": bool(f.disputed),
                "uncertain": uncertain,
                "narrative_value": f.narrative_value,
                "evidence_strength": f.evidence_strength,
                "supporting_text": f.supporting_text,
                "quote_status": f.quote_status,
                "speaker": f.speaker,
                "people": people,
                "locations": locations,
                "source_ids": source_ids,
                "chunk_ids": chunk_ids,
            }
        )
    timeline = [
        {"id": f"T{i:03d}", "event_date": f.event_date, "claim": f.claim}
        for i, f in enumerate([f for f in facts if f.event_date], 1)
    ]
    contra = [
        {
            "id": f"C{i:03d}",
            "topic": c.topic,
            "description": c.description,
            "severity": c.severity,
        }
        for i, c in enumerate(contradictions, 1)
    ]
    context = [
        {
            "id": f"CTX{i:03d}",
            "source": s.publisher or s.title,
            "language": s.language or "en",
            "summary": (s.summary_en or s.summary or "")[:800],
        }
        for i, s in enumerate(sources, 1)
        if s.summary or s.summary_en
    ]

    def _bucket(*prefixes: str) -> list[dict]:
        # Prefix match so legacy "human"/"scene" values and the v2
        # "human_detail"/"scene_detail" values share the same bucket.
        return [
            f for f in fact_items
            if (f["narrative_value"] or "").lower().startswith(prefixes)
        ]

    return {
        "facts": fact_items,
        "disputed_facts": [f for f in fact_items if f["uncertain"]],
        "timeline": timeline,
        "contradictions": contra,
        "approved_context": context,
        # Narrative-value buckets for evidence-aware editing (e.g. a critic's
        # ADD_HUMAN_DETAIL op can only draw from human_details, never invent).
        "human_details": _bucket("human"),
        "scene_details": _bucket("scene"),
        "investigation_details": _bucket("investigation"),
        "physical_evidence": _bucket("physical"),
        "historical_context": _bucket("context", "historical"),
        "environment_details": _bucket("environment"),
        "approved_quotes": _bucket("quote"),
        "research_gaps": [],
    }


_NV_BUCKET = {
    "human": "human_details",
    "scene": "scene_details",
    "investigation": "investigation_details",
    "physical": "physical_evidence",
    "environment": "environment_details",
    "quote": "approved_quotes",
}


def evidence_coverage_matrix(acts: list[dict], pack: dict) -> dict:
    """Per-act evidence depth — what each planned act actually has to work
    with. Far more informative than raw source counts."""
    by_id: dict[str, dict] = {}
    for key in ("facts", "timeline", "contradictions"):
        for item in pack.get(key) or []:
            by_id[item["id"]] = {"top": key, "item": item}
    for bucket, items in pack.items():
        if isinstance(items, list) and bucket.endswith(("_details", "_quotes", "_evidence")):
            for item in items:
                if isinstance(item, dict) and "id" in item:
                    by_id[item["id"]] = {"top": bucket, "item": item}

    matrix = {}
    for act in acts:
        counts = {
            "facts": 0, "timeline": 0, "contradictions": 0,
            "human_details": 0, "scene_details": 0,
            "investigation_details": 0, "physical_evidence": 0,
            "environment_details": 0, "quotes": 0, "context": 0,
        }
        for eid in act.get("evidence_ids") or []:
            entry = by_id.get(eid)
            if not entry:
                continue
            top = entry["top"]
            if top == "approved_quotes":
                counts["quotes"] += 1
            elif top == "facts":
                counts["facts"] += 1
                # A generic fact also counts toward its narrative bucket.
                nv = (entry["item"].get("narrative_value") or "").lower()
                for prefix, b in _NV_BUCKET.items():
                    if nv.startswith(prefix):
                        counts[
                            "quotes" if b == "approved_quotes" else b
                        ] += 1
                        break
                else:
                    if nv.startswith(("context", "historical")):
                        counts["context"] += 1
            elif top in counts:
                counts[top] += 1
        matrix[act.get("id") or f"act_{len(matrix)+1}"] = counts
    return matrix


def build_act_pack(pack: dict, evidence_ids: list[str] | set[str] | None) -> dict:
    """Smallest relevant evidence scope for one act (Part 17/18).

    When the director assigned evidence ids, an act's writer, grounding
    validator and repair editor all see only that subset plus context —
    not the entire case database."""
    assigned = set(evidence_ids or [])
    if not assigned:
        return pack

    def _items(key: str) -> list[dict]:
        return [i for i in (pack.get(key) or []) if i["id"] in assigned]

    return {
        "facts": _items("facts"),
        "disputed_facts": _items("disputed_facts"),
        "timeline": _items("timeline"),
        "contradictions": _items("contradictions"),
        "approved_context": pack.get("approved_context") or [],
        "human_details": _items("human_details"),
        "scene_details": _items("scene_details"),
        "investigation_details": _items("investigation_details"),
        "physical_evidence": _items("physical_evidence"),
        "historical_context": _items("historical_context"),
        "environment_details": _items("environment_details"),
        "approved_quotes": _items("approved_quotes"),
        "research_gaps": pack.get("research_gaps") or [],
    }


def estimate_narrative_capacity(
    facts: list[Fact],
    contradictions: list[Contradiction],
    sources: list[Source],
    requested_minutes: int,
) -> dict:
    """Estimate how many narration minutes the evidence can honestly support.

    Depth-weighted and deliberately conservative (Part 12/13): full-text
    sources and evidence-item diversity drive the estimate — 30
    summary-only sources never outrank a few rich primary documents.
    Better to under-promise duration than force the writer to invent.
    """
    cfg = ai_config.research_depth

    depth = {
        "full_text_sources": 0,
        "partial_text_sources": 0,
        "summary_only_sources": 0,
        "metadata_sources": 0,
    }
    for s in sources:
        status = s.content_status or "summary_only"
        if status == "full_text":
            depth["full_text_sources"] += 1
        elif status == "partial_text":
            depth["partial_text_sources"] += 1
        elif status in ("metadata_only", "unavailable"):
            depth["metadata_sources"] += 1
        else:
            depth["summary_only_sources"] += 1

    nv_counts: dict[str, int] = {}
    for f in facts:
        nv = (f.narrative_value or "fact").lower()
        nv_counts[nv] = nv_counts.get(nv, 0) + 1

    def _nv(*prefixes: str) -> int:
        return sum(v for k, v in nv_counts.items()
                   if any(k.startswith(p) for p in prefixes))

    depth["evidence_items"] = len(facts)
    depth["human_details"] = _nv("human")
    depth["scene_details"] = _nv("scene", "environment")
    depth["investigation_details"] = _nv("investigation", "procedure", "physical")
    depth["quotes"] = _nv("quote")
    depth["timeline_events"] = sum(1 for f in facts if f.event_date)
    depth["contradictions"] = len(contradictions)

    families = {
        s.source_family for s in sources if s.source_family
    }
    depth["independent_source_families"] = (
        len(families) if families
        else len({(s.publisher or "", s.title[:40]) for s in sources})
    )

    evidence_minutes = min(
        depth["evidence_items"] * cfg.minutes_per_evidence_item,
        cfg.max_evidence_item_minutes,
    )
    minutes = (
        depth["full_text_sources"] * cfg.minutes_per_fulltext_source
        + depth["partial_text_sources"] * cfg.minutes_per_partial_source
        + depth["summary_only_sources"] * cfg.minutes_per_summary_source
        + depth["metadata_sources"] * cfg.minutes_per_metadata_source
        + evidence_minutes
        + depth["human_details"] * cfg.minutes_per_human_detail
        + depth["scene_details"] * cfg.minutes_per_scene_detail
        + depth["investigation_details"] * cfg.minutes_per_investigation_detail
        + depth["quotes"] * cfg.minutes_per_quote
        + depth["timeline_events"] * cfg.minutes_per_timeline_event
        + depth["contradictions"] * cfg.minutes_per_contradiction
        + depth["independent_source_families"]
        * cfg.minutes_per_independent_family
    )
    supported = round(minutes, 1)
    needed = requested_minutes * cfg.minimum_capacity_ratio

    weak_areas = []
    weak_map = {
        "human_details": ("human detail", "human_details"),
        "scene_details": ("scene texture", "scene_details"),
        "approved_quotes": ("quotes", "quotes"),
        "investigation_details": ("investigation detail", "investigation_details"),
    }
    for cfg_key, (label, depth_key) in weak_map.items():
        if depth[depth_key] < cfg.weak_area_minimums.get(cfg_key, 0):
            weak_areas.append(label)
    if depth["full_text_sources"] == 0 and depth["partial_text_sources"] == 0:
        weak_areas.append("deep source text")

    # Confidence: how trustworthy the estimate itself is. Diversity of
    # evidence types and real text depth raise it; summary-only pools cap it.
    diversity = sum(
        1 for k in ("human_details", "scene_details", "investigation_details",
                    "quotes", "contradictions") if depth[k] > 0
    ) / 5.0
    text_depth = min(
        1.0,
        (depth["full_text_sources"] * 2 + depth["partial_text_sources"]) / 6.0,
    )
    confidence = round(0.3 + 0.45 * diversity + 0.25 * text_depth, 2)

    return {
        "requested_minutes": requested_minutes,
        "estimated_supported_minutes": supported,
        "minimum_required_minutes": round(needed, 1),
        "confidence": confidence,
        "depth": depth,
        "weak_areas": weak_areas,
        "status": (
            "ready" if supported >= needed
            else "insufficient_for_requested_length"
        ),
    }


def research_gap_plan(
    facts: list[Fact],
    contradictions: list[Contradiction],
    sources: list[Source],
    requested_minutes: int,
) -> dict:
    """Targeted ResearchGapPlan (Part 14): what evidence categories are
    missing, with reasons a follow-up research pass can chase."""
    cap = estimate_narrative_capacity(
        facts, contradictions, sources, requested_minutes
    )
    depth = cap["depth"]
    missing = []

    def _gap(kind: str, priority: str, reason: str):
        missing.append({"type": kind, "priority": priority, "reason": reason})

    if depth["human_details"] < 3:
        _gap(
            "human_detail",
            "high",
            "Little documented personal context — roles, families, routines, "
            "correspondence — for the people in this case.",
        )
    if depth["scene_details"] < 3:
        _gap(
            "scene_detail",
            "high",
            "Few documented physical observations (layout, weather, objects, "
            "measurements) — scenes cannot be narrated without them.",
        )
    if depth["investigation_details"] < 2:
        _gap(
            "investigation_detail",
            "medium",
            "Thin procedural/investigative record — official reports, "
            "inquiries or police statements may exist.",
        )
    if depth["quotes"] < 1:
        _gap(
            "quote",
            "medium",
            "No verified direct quotations — witness statements, interviews "
            "or official correspondence would help.",
        )
    if depth["timeline_events"] < max(5, len(facts) // 3):
        _gap(
            "timeline",
            "medium",
            "Chronology is sparse — dated primary records (logs, reports, "
            "court filings) needed.",
        )
    if depth["full_text_sources"] == 0 and depth["partial_text_sources"] < 3:
        _gap(
            "full_text_source",
            "high",
            "No retrieved source text — seek public records, official "
            "archives, transcripts and open reporting.",
        )
    return {
        "capacity": cap,
        "missing": missing,
        "status": (
            "sufficient"
            if cap["status"] == "ready" and not missing
            else "research_gaps_present"
        ),
    }


def _parse_sections(raw: str) -> list[dict]:
    """Split writer output on `[[ACT:id]]` marker lines.

    Returns [{"id": str, "text": str}] in order. Text without any markers
    becomes a single "full" section so downstream stages still work.
    """
    prefix = ai_config.story_quality.section_marker_prefix
    marker = re.compile(rf"^\s*{re.escape(prefix)}([^\]]+)\]\]\s*$", re.M)
    parts = marker.split(raw)
    if len(parts) == 1:
        return [{"id": "full", "text": raw.strip()}]
    sections = []
    # parts[0] is preamble before the first marker (kept if non-trivial)
    if parts[0].strip():
        sections.append({"id": "prologue", "text": parts[0].strip()})
    for sid, body in zip(parts[1::2], parts[2::2]):
        body = body.strip()
        if body:
            sections.append({"id": sid.strip()[:40], "text": body})
    return sections or [{"id": "full", "text": raw.strip()}]


def _join_sections(sections: list[dict], strip: bool = True) -> str:
    text = "\n\n".join(s["text"].strip() for s in sections if s["text"].strip())
    return strip_narration_artifacts(text) if strip else text


def _mark_sections(sections: list[dict]) -> str:
    """Internal-only marked rendering so critics can address sections."""
    marker = ai_config.story_quality.section_marker_prefix
    return "\n\n".join(
        f"{marker}{s['id']}]]\n\n{s['text']}" for s in sections
    )


# ---------------------------------------------------------------------------
# Section (act) structure must survive every edit step: voice blocks,
# per-language timelines and reveal planning all need to know which text
# belongs to which act. These helpers carry the structure through
# repairs, polish passes, rewrites and localization.
# ---------------------------------------------------------------------------

_MARKER_INSTRUCTION = (
    "Lines of the form [[ACT:<id>]] are structural markers. Keep every "
    "marker line exactly as given, on its own line, in the same order, "
    "and keep each passage under its own marker. Never add, rename, "
    "merge or drop markers."
)


def _marker_re() -> re.Pattern:
    prefix = ai_config.story_quality.section_marker_prefix
    return re.compile(rf"^\s*{re.escape(prefix)}([^\]]+)\]\]\s*$", re.M)


def strip_section_markers(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _marker_re().sub("", text or "")).strip()


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _norm_ws(text: str) -> str:
    return " ".join((text or "").split())


def is_structured(sections: list[dict]) -> bool:
    """True when the sections are real acts, not the single 'full' blob."""
    return bool(sections) and not (
        len(sections) == 1 and sections[0].get("id") == "full"
    )


def clean_sections(sections: list[dict]) -> list[dict]:
    """Per-section artifact stripping, so joined sections equal the
    stored story text (which is stripped the same way)."""
    out = []
    for s in sections:
        t = strip_narration_artifacts(s.get("text") or "")
        if t.strip():
            out.append({"id": s["id"], "text": t.strip()})
    return out


def realign_sections(
    previous: list[dict], new_raw: str, allow_paragraph_fallback: bool = True
) -> tuple[list[dict], bool]:
    """Map an edited/rewritten/localized text back onto the previous
    section ids.

    1. Markers present and in the same order -> use them.
    2. (same-language edits only) no usable markers but the paragraph
       count is unchanged -> assign paragraphs by the previous counts.
    3. Otherwise -> a single 'full' section and ok=False, so the caller
       can record that this step lost the act structure.
    """
    prev_ids = [s["id"] for s in previous]

    def _carry(new: list[dict]) -> list[dict]:
        # Keep per-act metadata (evidence ids, grounding) on the new text.
        return [
            {**{k: v for k, v in p.items() if k != "text"}, "text": n["text"]}
            for p, n in zip(previous, new)
        ]

    parsed = _parse_sections(new_raw)
    ids = [s["id"] for s in parsed]
    if ids == prev_ids:
        return _carry(parsed), True
    if ids[:1] == ["prologue"] and ids[1:] == prev_ids:
        # A model-added lead-in before the first marker belongs to act 1.
        parsed[1] = dict(
            parsed[1], text=parsed[0]["text"] + "\n\n" + parsed[1]["text"]
        )
        return _carry(parsed[1:]), True
    plain = strip_section_markers(new_raw)
    if allow_paragraph_fallback and is_structured(previous):
        paras = _paragraphs(plain)
        counts = [len(_paragraphs(s["text"])) for s in previous]
        if all(counts) and len(paras) == sum(counts):
            out, k = [], 0
            for c in counts:
                out.append({"text": "\n\n".join(paras[k:k + c])})
                k += c
            return _carry(out), True
    return [{"id": "full", "text": plain}], not is_structured(previous)


def stored_sections(version: StoryVersion) -> list[dict] | None:
    """The act sections saved with a StoryVersion — only when they add up
    to exactly the approved story text (what gets narrated must be what
    passed the gates). None for legacy versions without section text."""
    try:
        struct = json.loads(version.narrative_structure or "{}")
    except (ValueError, TypeError):
        return None
    secs = struct.get("sections") if isinstance(struct, dict) else None
    if not secs or any(not isinstance(s, dict) or "text" not in s for s in secs):
        return None
    sections = [{"id": str(s["id"]), "text": s["text"]} for s in secs]
    joined = "\n\n".join(s["text"] for s in sections)
    if _norm_ws(joined) != _norm_ws(version.story_text):
        return None
    return sections


def structure_without_text(structure: dict) -> dict:
    """A stored narrative_structure minus the section TEXT (ids and word
    counts stay). Use whenever the structure is sent to a model or reused
    as a plan — the text is the story itself and would double the tokens."""
    if not isinstance(structure, dict) or not structure.get("sections"):
        return structure
    return dict(
        structure,
        sections=[
            {"id": s.get("id"), "words": s.get("words")}
            for s in structure["sections"]
            if isinstance(s, dict)
        ],
    )


def stored_evidence_fingerprint(version: StoryVersion) -> str | None:
    try:
        struct = json.loads(version.narrative_structure or "{}")
    except (ValueError, TypeError):
        return None
    return struct.get("evidence_fingerprint") if isinstance(struct, dict) else None


def current_evidence_fingerprint(db: Session, case_id: int) -> str:
    return evidence_fingerprint(
        build_evidence_pack(
            db.query(Fact).filter(Fact.case_id == case_id).all(),
            db.query(Contradiction).filter(Contradiction.case_id == case_id).all(),
            db.query(Source).filter(Source.case_id == case_id).all(),
        )
    )


def _evidence_usage(sections: list[dict], pack: dict) -> dict:
    """Coverage report: which pack ids each section's plan assigned."""
    all_ids = set()
    for key in ("facts", "timeline", "contradictions"):
        all_ids.update(i["id"] for i in pack.get(key) or [])
    used: set[str] = set()
    for s in sections:
        used.update(s.get("meta", {}).get("evidence_ids") or [])
    used &= all_ids
    return {
        "used_evidence_ids": sorted(used),
        "unused_evidence_ids": sorted(all_ids - used),
        "coverage": round(len(used) / len(all_ids), 3) if all_ids else 0.0,
    }


class StoryDirector:
    def __init__(self):
        self.gen = get_generation_provider()

    async def design(
        self,
        case: Case,
        pack: dict,
        target_minutes: int,
        language: str,
        role: str = "story_director",
    ) -> tuple[dict, GenerationResult]:
        system = """
You are a documentary story director designing a long-form true-crime episode.

Core principle — EXPERIENCE THE MYSTERY FIRST, UNDERSTAND IT SECOND,
DEBUNK IT THIRD:

- ACT 1 "The Absence": open on a concrete event and a clear anomaly within
  the first 30-45 seconds. Establish the central question immediately.
  No philosophy, no long atmosphere, no mythology explanation.
- ACT 2 "The Last Known World": reconstruct only what evidence allows;
  build timeline; make the people human via names, roles, duties, family
  status, documented behaviour — never invented inner feelings.
- ACT 3 "The Investigation": official observations, physical evidence,
  the official theory and its contradictions; let the audience feel the
  explanation is incomplete.
- ACT 4 "The Story That Grew": ONLY now introduce sensational claims,
  fabrications, myths and dramatizations — as a reveal that part of what
  the audience "knows" was never real evidence.
- ACT 5 "What Remains": return to the real people; separate what we know,
  what is plausible, what stays unknowable; end on a strong factual image
  or question.

Anti-AI-style rules: no repeated symbolic motifs (silence, darkness,
bureaucracy, "the sea knows"), no ornate clause chains, no repeated
rhetorical questions, no generic cinematic metaphors. Prefer precise,
controlled, visual narration.

Return JSON only:
{
  "title": "...",
  "central_question": "...",
  "hook_design": "the concrete first-45-seconds",
  "acts": [
    {"id": "act1", "title": "...", "purpose": "...",
     "key_beats": ["..."], "target_words": 1200,
     "evidence_ids": ["F001", "T002"],
     "open_loops": ["question this act opens and leaves unresolved"],
     "resolved_loops": ["earlier loop this act answers"],
     "do_not_reveal": ["evidence ids reserved for later acts"]}
  ],
  "open_loops": ["..."],
  "reveal_map": ["when and where each contradiction surfaces"],
  "ending_strategy": "...",
  "human_threads": ["supported humanizing details to weave in early"]
}

Act rules:
- acts[].target_words must sum to roughly the total target.
- acts[].evidence_ids assigns evidence to the act where it belongs — do
  not dump everything into act 1; reserve myth/fabrication evidence for
  the debunk act; an id may appear in at most one act. Assign fact,
  timeline AND contradiction ids (F…, T…, C…) to the acts that own them.
- do_not_reveal lists evidence the writer must withhold until a later act.
"""
        user = json.dumps(
            {
                "case": case.canonical_title,
                "target_minutes": target_minutes,
                "language": language,
                "evidence": pack,
            },
            ensure_ascii=False,
        )
        data, res = await self.gen.generate_structured(role, system, user)
        return data, res


class WriterAgent:
    def __init__(self):
        self.gen = get_generation_provider()

    async def write(
        self,
        case: Case,
        pack: dict,
        plan: dict,
        target_minutes: int,
        language: str,
        tone: str,
        role: str = "writer",
        words_per_minute: int | None = None,
    ) -> tuple[str, GenerationResult]:
        target_words = int(
            target_minutes * (words_per_minute or ai_config.story.words_per_minute)
        )
        marker = ai_config.story_quality.section_marker_prefix

        system = f"""
You are an elite long-form true-crime writer.

Write in language: {language}
Tone: {tone}

GROUNDING — the hard contract:
- You may use ONLY details contained in the supplied evidence pack
  (facts, timeline, contradictions, approved_context).
- NEVER introduce remembered historical facts, measurements, weather
  values, dates, architectural or physical details, quotations,
  biographical or procedural details that are not in the evidence pack.
- If useful context is missing: omit it. Do NOT fill gaps from your own
  knowledge.
- Items flagged "uncertain"/"disputed" MUST be narrated with uncertainty
  language ("reports differ", "according to one account", "it is not
  known") — never presented as certain.

Composition rules:
- No invented quotes, dialogue, evidence, motives, or scenes.
- No citations, URLs, source names, evidence IDs or research notes.
- No markdown headings or separator lines in the narration itself.
- No symbolic-motif repetition (silence, darkness, "the sea knows");
  precise, controlled, visual prose.
- Aim for about {target_words} words total.

Format: write the story act by act following the plan. Put a marker line
`{marker}<act_id>]]` alone on its own line immediately before each act's
narration. Markers are internal structure, not part of the narration.
Output only markers plus story text — nothing else.
"""

        user = json.dumps(
            {
                "case": case.canonical_title,
                "narrative_plan": plan,
                "evidence": pack,
            },
            ensure_ascii=False,
        )
        res = await self.gen.generate_text(role, system, user)
        return res.text, res

    async def write_act(
        self,
        case: Case,
        pack: dict,
        plan: dict,
        act: dict,
        prior_position: str,
        later_reserved: list[str],
        language: str,
        tone: str,
        role: str = "writer",
        already_narrated: list[str] | None = None,
    ) -> tuple[str, GenerationResult]:
        """Write ONE act against its word budget and assigned evidence.

        Keeps each generation call small, bounds context drift and prevents
        premature reveals — the act only sees its own assigned evidence.
        """
        act_pack = build_act_pack(pack, act.get("evidence_ids"))

        system = f"""
You are an elite long-form true-crime writer producing ONE act of a
multi-act documentary episode.

Write in language: {language}
Tone: {tone}

GROUNDING — the hard contract:
- You may use ONLY details contained in the supplied evidence pack
  (facts, timeline, contradictions, approved_context).
- NEVER introduce remembered historical facts, measurements, weather
  values, dates, architectural or physical details, quotations,
  biographical or procedural details that are not in the evidence pack.
- Items flagged "uncertain"/"disputed" MUST be narrated with uncertainty
  language — never presented as certain.
- Absence of records, failed searches or unproven theories are NEVER
  proof — narrate what was searched and found, not conclusions the
  evidence does not support.

Act rules:
- This act's target is ~{act.get('target_words')} words.
- Purpose: {act.get('purpose', '')}
- Narrate only this act's assigned evidence; do not use or foreshadow
  evidence assigned to later acts.
- "already_narrated" lists evidence earlier acts have ALREADY told the
  audience. Never re-explain those facts; reference them in at most one
  short clause where continuity requires it. New information only.
- Open loops this act may raise: {act.get('open_loops') or []}
- Loops this act must resolve: {act.get('resolved_loops') or []}
- No invented quotes, dialogue, evidence, motives, or scenes.
- No citations, URLs, source names, evidence IDs, headings or markers.
- No symbolic-motif repetition; precise, controlled, visual prose.
Output only this act's narration text — nothing else.
"""
        user = json.dumps(
            {
                "case": case.canonical_title,
                "act": {k: act.get(k) for k in
                        ("id", "title", "purpose", "key_beats",
                         "target_words", "open_loops", "resolved_loops",
                         "do_not_reveal")},
                "episode_position": prior_position,
                "reserved_for_later_acts": later_reserved,
                "already_narrated": already_narrated or [],
                "evidence": act_pack,
            },
            ensure_ascii=False,
        )
        res = await self.gen.generate_text(role, system, user)
        return res.text, res


class EngagementCritic:
    def __init__(self):
        self.gen = get_generation_provider()

    async def critique(
        self,
        story: str,
        target_minutes: int,
        role: str = "engagement_critic",
        marked_story: str | None = None,
    ) -> tuple[dict, GenerationResult]:
        system = """
You are a ruthless story editor.

Evaluate:
1. hook strength
2. curiosity gaps
3. pacing
4. clarity
5. emotional stakes
6. unnecessary exposition
7. reveal timing
8. ending strength
9. ethical restraint
10. whether a viewer is likely to keep watching

Return JSON only:
{
  "score": 0-100,
  "dimensions": {
    "hook": 0-100,
    "pacing": 0-100,
    "curiosity": 0-100,
    "clarity": 0-100,
    "emotional_stakes": 0-100,
    "reveal_timing": 0-100,
    "ending": 0-100,
    "repetition": 0-100
  },
  "problems": ["..."],
  "rewrite_instructions": ["..."],
  "sections": [
    {"section_id": "act1", "score": 0-100,
     "problems": [
       {"type": "REORDER|EXPAND|CONDENSE|REPHRASE|STRENGTHEN_HOOK|"
                "DELAY_REVEAL|ADD_HUMAN_DETAIL|CLARIFY|REMOVE_REPETITION|"
                "IMPROVE_TRANSITION",
        "severity": "low|medium|high",
        "instruction": "...",
        "preserve_evidence_ids": ["F001"]}
     ]}
  ]
}

For "repetition", a high score means little unwanted repetition.
In "sections", reference the section markers ([[ACT:id]]) visible in the
story; every operation must name its section_id and should point at the
specific paragraph or span it concerns. Only request ADD_HUMAN_DETAIL or
EXPAND if unused evidence supports it.
"""
        data, res = await self.gen.generate_structured(
            role,
            system,
            json.dumps(
                {
                    "target_minutes": target_minutes,
                    "story": marked_story or story,
                },
                ensure_ascii=False,
            ),
        )
        return data, res

    async def critique_section(
        self, section_title: str, section_text: str, position: str
    ) -> tuple[dict, GenerationResult]:
        system = """
You are a ruthless documentary editor reviewing ONE section of a
long-form true-crime episode. Judge whether this section keeps a viewer
watching: information density, pacing, curiosity, any drop-off risk.

Return JSON only:
{
  "section": "<echoed title>",
  "hook": 0-100,
  "pacing": 0-100,
  "curiosity": 0-100,
  "repetition": 0-100,
  "information_density": 0-100,
  "score": 0-100,
  "drop_off_risk": "low|medium|high",
  "problems": ["..."]
}
"""
        data, res = await self.gen.generate_structured(
            "section_critic",
            system,
            json.dumps(
                {
                    "position": position,
                    "section": section_title,
                    "text": section_text,
                },
                ensure_ascii=False,
            ),
        )
        return data, res


class SimilarityCritic:
    @staticmethod
    def rough_similarity(story: str, source_texts: list[str]) -> float:
        if not source_texts:
            return 0.0

        scores = []
        chunk = story[:8000]
        for text in source_texts:
            if not text:
                continue
            scores.append(fuzz.token_set_ratio(chunk, text[:8000]) / 100.0)

        return max(scores) if scores else 0.0


class StoryPipeline:
    def __init__(self, roles: dict | None = None):
        self.director = StoryDirector()
        self.writer = WriterAgent()
        self.critic = EngagementCritic()
        self.gen = get_generation_provider()
        # Logical stage -> configured role. The master pipeline swaps in
        # master_* roles (routed independently in ai_config).
        self.roles = roles or {
            "director": "story_director",
            "writer": "writer",
            "rewriter": "rewriter",
            "critic": "engagement_critic",
            "final_editor": "final_editor",
        }

    # ------------------------------------------------------------------
    # main pipeline
    # ------------------------------------------------------------------

    async def run(
        self,
        db: Session,
        case: Case,
        target_minutes: int,
        language: str,
        tone: str,
        iterations: int,
        kind: str = "direct",
        words_per_minute: int | None = None,
    ) -> StoryVersion:
        facts = db.query(Fact).filter(Fact.case_id == case.id).all()
        contradictions = db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
        sources = db.query(Source).filter(Source.case_id == case.id).all()

        if not facts:
            raise RuntimeError("No facts found. Run research first.")

        iterations = min(iterations, ai_config.story.max_rewrite_iterations)
        mg = ai_config.master_generation
        pack = build_evidence_pack(facts, contradictions, sources)
        wpm = words_per_minute or ai_config.story.words_per_minute
        target_words = int(target_minutes * wpm)
        run_started = utc_now()
        revision_log: list[dict] = []
        structure_lost_at: list[str] = []
        budget_exhausted = False

        previous_status = case.status
        case.status = "writing"
        db.commit()
        try:
            with track_run(db, case.id, "Story Director") as run:
                plan, res = await self.director.design(
                    case, pack, target_minutes, language,
                    role=self.roles["director"],
                )
                stamp_run(run, res, self.roles["director"])
                run.output_summary = f"acts={len(plan.get('acts', []))}"
            self._normalize_act_budgets(plan, target_words)

            acts = plan.get("acts") or []
            if mg.section_based_generation and len(acts) > 1:
                sections, story_res = await self._write_acts(
                    db, case, pack, plan, acts, language, tone
                )
            else:
                with track_run(
                    db, case.id, "Writer",
                    input_summary=f"target={target_minutes}min lang={language} tone={tone} kind={kind}",
                ) as run:
                    raw, story_res = await self.writer.write(
                        case, pack, plan, target_minutes, language, tone,
                        role=self.roles["writer"], words_per_minute=wpm,
                    )
                    stamp_run(run, story_res, self.roles["writer"])
                    run.output_summary = f"words={len(raw.split())}"
                sections = _parse_sections(raw)

            # --- per-section grounding repair, before assembly-level gates ---
            # Validation/repair use the act-scoped pack (Part 18), not the
            # whole case database.
            max_g_passes = ai_config.story_quality.max_grounding_repair_iterations + 1
            # Repair-call ceiling: grounding repair must not consume the
            # entire case budget before engagement cycles ever run — the
            # assembled gate still judges whatever repairs did not reach.
            max_repairs = ai_config.master_generation.max_span_repairs_per_pass * 5
            repairs_done = 0
            for i, s in enumerate(sections):
                act_pack = build_act_pack(
                    pack, (s.get("meta") or {}).get("evidence_ids")
                )
                try:
                    rep = await self._grounding_check_only(
                        db, case, s["text"], act_pack
                    )
                except Exception:
                    # A failed check must not kill the run — the assembled
                    # grounding gate still decides honestly downstream.
                    rep = {}
                    revision_log.append(
                        {"section_id": s["id"], "repaired": False,
                         "reason": "grounding_check_failed"}
                    )
                for _ in range(max_g_passes):
                    if not self._grounding_fails(rep):
                        break
                    if repairs_done >= max_repairs:
                        revision_log.append(
                            {"section_id": s["id"], "repaired": False,
                             "reason": "repair_budget_exhausted"}
                        )
                        break
                    try:
                        new_text, rlog = await self._repair_section_spans(
                            db, case, s, rep, act_pack, language,
                            limit=max_repairs - repairs_done,
                        )
                    except Exception:
                        # A failed repair must not kill the run — keep the
                        # original text and let the assembled grounding gate
                        # decide honestly downstream.
                        revision_log.append(
                            {"section_id": s["id"], "repaired": False,
                             "reason": "repair_call_failed"}
                        )
                        break
                    revision_log.extend(rlog)
                    repairs_done += sum(
                        1 for e in rlog
                        if e.get("repaired") or e.get("rewrite_rejected")
                    )
                    if new_text == s["text"]:
                        break  # repairs exhausted or rejected — stop
                    s["text"] = new_text
                    try:
                        rep = await self._grounding_check_only(
                            db, case, new_text, act_pack
                        )
                    except Exception:
                        # Re-validation must not kill the run — keep the
                        # text and let the assembled gate decide.
                        rep = {"validator_error": True}
                        revision_log.append(
                            {"section_id": s["id"], "repaired": True,
                             "reason": "grounding_recheck_failed"}
                        )
                        break
                s["grounding"] = rep

            sections, section_scores = await self._section_pass(
                db, case, sections, pack, plan, language
            )

            text = _join_sections(sections)
            grounding = await self._checked_grounding(db, case, text, pack)
            consistency = await self._checked_consistency(db, case, text, pack)

            # --- consistency repair: high/medium violations get surgical
            # span fixes on the assembled text — detection alone never
            # clears the gate (Master_Prompt step 18). One bounded pass.
            cons_violations = consistency.get("violations") or []
            repairable = [
                v for v in cons_violations
                if v.get("severity") in ("high", "medium")
            ][: ai_config.master_generation.max_span_repairs_per_pass]
            if repairable:
                # Repair on the MARKED text: paragraph-level span fixes
                # leave the [[ACT:id]] lines untouched, so the act
                # structure survives the repair.
                marked = _mark_sections(sections)
                pseudo = {"id": "assembled", "text": marked}
                report = {
                    "unsupported_claims": [
                        {
                            "claim": v.get("detail") or "",
                            "exact_text_span": v.get("location") or "",
                            "reason": (
                                f"consistency violation "
                                f"({v.get('type')}, {v.get('severity')}): "
                                f"{v.get('detail')}"
                            ),
                            "nearest_supported_evidence_ids": [],
                        }
                        for v in repairable
                    ]
                }
                try:
                    new_marked, clog = await self._repair_section_spans(
                        db, case, pseudo, report, pack, language
                    )
                except Exception:
                    clog = [{"stage": "consistency_repair",
                             "reason": "repair_call_failed"}]
                    new_marked = marked
                revision_log.extend(
                    {**e, "stage": "consistency_repair"} for e in clog
                )
                if new_marked != marked:
                    sections, kept = realign_sections(sections, new_marked)
                    if not kept:
                        structure_lost_at.append("consistency_repair")
                    text = _join_sections(sections)
                    consistency = await self._checked_consistency(
                        db, case, text, pack
                    )
                    grounding = await self._checked_grounding(
                        db, case, text, pack
                    )

            # --- engagement revision cycles: targeted ops, not full rewrites ---
            critique = {}
            prev_snapshot = None
            score_history: list[float] = []
            no_gain_streak = 0
            cycles = min(iterations, mg.max_revision_cycles)
            for _ in range(cycles):
                text = _join_sections(sections)
                h = text_hash(text)
                marked = _mark_sections(sections)
                with track_run(db, case.id, "Engagement Critic") as run:
                    try:
                        critique, res = await self.critic.critique(
                            text, target_minutes, role=self.roles["critic"],
                            marked_story=marked,
                        )
                    except Exception:
                        # Critic outage ends revision cycles — the gates
                        # still evaluate the last good text honestly.
                        run.output_summary = "critique_failed"
                        break
                    stamp_run(run, res, self.roles["critic"], text_hash=h)
                    run.output_summary = f"score={critique.get('score', 0)} hash={h[:8]}"
                score = float(critique.get("score", 0))
                if score >= ai_config.story.engagement_threshold:
                    break
                score_history.append(score)

                # Acceptance: the previous cycle's candidate only survives if
                # engagement improved by the configured delta and hard
                # metrics did not regress; otherwise roll it back.
                if prev_snapshot is not None:
                    delta = score - prev_snapshot["score"]
                    if delta < mg.minimum_engagement_improvement:
                        sections = prev_snapshot["sections"]
                        grounding = prev_snapshot["grounding"]
                        consistency = prev_snapshot["consistency"]
                        revision_log.append(
                            {
                                "candidate_rejected": True,
                                "reason": "insufficient_engagement_improvement",
                                "score_delta": round(delta, 1),
                            }
                        )
                        no_gain_streak += 1
                        if no_gain_streak >= 2:
                            break
                        continue

                spent_tokens, spent_cost = self._spend_since(
                    db, case.id, run_started
                )
                if (
                    spent_tokens >= ai_config.cost_control.max_master_tokens_per_case
                    or spent_cost >= ai_config.cost_control.max_master_cost_usd
                ):
                    budget_exhausted = True
                    break

                prev_snapshot = {
                    "sections": copy.deepcopy(sections),
                    "score": score,
                    "grounding": grounding,
                    "consistency": consistency,
                }
                sections, edits, section_scores = await self._apply_revision_ops(
                    db, case, sections, section_scores, pack, plan,
                    critique, language,
                )
                revision_log.extend(edits)
                if any(e.get("accepted") for e in edits):
                    text = _join_sections(sections)
                    grounding = await self._checked_grounding(
                        db, case, text, pack
                    )
                    consistency = await self._checked_consistency(
                        db, case, text, pack
                    )
                else:
                    # Nothing could be improved — further cycles repeat ops.
                    break

            # --- gates + optional final edit ---
            text = _join_sections(sections)
            gates = self._evaluate_gates(
                text, language, target_minutes, grounding, consistency,
                float(critique.get("score", 0)),
                wpm=words_per_minute,
            )
            if gates["pass"]:
                structured = is_structured(sections)
                try:
                    edited, editor_res = await self._final_edit(
                        db, case,
                        _mark_sections(sections) if structured else text,
                        language, marked=structured,
                    )
                except Exception:
                    # Editor outage must not discard a passing draft.
                    edited, editor_res = None, None
                    revision_log.append(
                        {"rewrite_rejected": True,
                         "reason": "final_editor_failed",
                         "stage": "final_editor"}
                    )
                edit_sections: list[dict] = []
                edit_text = ""
                if edited is not None:
                    edit_sections, kept = realign_sections(sections, edited)
                    edit_text = _join_sections(edit_sections)
                    if not kept:
                        # Polish is optional; the act structure that voice
                        # blocks and timelines depend on is not.
                        revision_log.append(
                            {"rewrite_rejected": True,
                             "reason": "structure_lost",
                             "stage": "final_editor"}
                        )
                        edited = None
                orig_words = len(text.split())
                tol = mg.rewrite_length_tolerance
                if edited is not None and not (
                    orig_words * (1 - tol)
                    <= len(edit_text.split())
                    <= orig_words * (1 + tol)
                ):
                    # Length contract violation — keep the passing text.
                    revision_log.append(
                        {
                            "rewrite_rejected": True,
                            "reason": "length_violation",
                            "stage": "final_editor",
                        }
                    )
                    edited = None
                if edited is not None:
                    # Fail-closed: a validator error rejects the edit
                    # rather than killing the whole run.
                    edit_grounding = await self._checked_grounding(
                        db, case, edit_text, pack
                    )
                    edit_consistency = await self._checked_consistency(
                        db, case, edit_text, pack
                    )
                    edit_gates = self._evaluate_gates(
                        edit_text, language, target_minutes,
                        edit_grounding, edit_consistency,
                        float(critique.get("score", 0)),
                        wpm=words_per_minute,
                    )
                    # Never let the final polish break a passing story.
                    if edit_gates["pass"]:
                        text = edit_text
                        sections = edit_sections
                        story_res = editor_res
                        gates = edit_gates
                        grounding = edit_grounding
                        consistency = edit_consistency

            # --- FINAL critic pass on the exact text being stored ---
            final_hash = text_hash(text)
            with track_run(db, case.id, "Engagement Critic") as run:
                try:
                    final_critique, res = await self.critic.critique(
                        text, target_minutes, role=self.roles["critic"]
                    )
                except Exception:
                    # A critic outage must not discard a finished draft —
                    # store it with score 0 so gates fail honestly.
                    final_critique = {"score": 0, "critic_error": True}
                    run.output_summary = "critique_failed"
                    res = None
                else:
                    stamp_run(run, res, self.roles["critic"], text_hash=final_hash)
                    run.output_summary = (
                        f"score={final_critique.get('score', 0)} hash={final_hash[:8]} final"
                    )
            if float(final_critique.get("score", 0)) < ai_config.story.engagement_threshold:
                failures = gates.setdefault("failures", [])
                if "engagement_below_threshold" not in failures:
                    failures.append("engagement_below_threshold")
                gates["pass"] = False
            final_critique["quality_gates"] = gates
            final_critique["section_scores"] = section_scores
            final_critique["consistency"] = consistency
            final_critique["text_hash"] = final_hash
            final_critique["revision_log"] = revision_log
            final_critique["score_history"] = score_history
            final_critique["budget_exhausted"] = budget_exhausted

            similarity, sim_status = self._score_similarity(db, case, text, sources)
            # Phrase-level originality against authorized source texts —
            # vocabulary overlap (token_set) stays telemetry-only because
            # faithful same-case coverage saturates it without any copying.
            from app.services.similarity import check_source_text_similarity
            src_sim = check_source_text_similarity(db, case.id, text)
            final_critique["source_text_similarity"] = src_sim
            if src_sim["status"] == "fail":
                final_critique["similarity_flagged"] = True
                gates["pass"] = False
                failures = gates.setdefault("failures", [])
                if "source_phrase_overlap" not in failures:
                    failures.append("source_phrase_overlap")

            # Source-separation gates (Master_Prompt 16/17): the story must
            # share the case's facts but never a transcript's phrasing or
            # structure. Deterministic checks — no provider cost; skipped
            # entirely when the case has no transcript corpus.
            from app.services.similarity import (
                check_structure_similarity,
                check_text_similarity,
            )
            t_sim = check_text_similarity(db, case.id, text)
            s_sim = check_structure_similarity(db, case.id, sections)
            final_critique["transcript_similarity"] = t_sim
            final_critique["structure_similarity"] = s_sim
            failures = gates.setdefault("failures", [])
            if t_sim["status"] == "fail":
                final_critique["similarity_flagged"] = True
                gates["pass"] = False
                if "transcript_phrase_overlap" not in failures:
                    failures.append("transcript_phrase_overlap")
            if s_sim["status"] == "fail":
                gates["pass"] = False
                if "structural_similarity" not in failures:
                    failures.append("structural_similarity")

            row = self._new_version(
                db, case, plan, sections, text,
                float(final_critique.get("score", 0)),
                similarity, sim_status, final_critique, language, story_res,
                kind=kind,
                extra_structure={
                    "evidence_usage": _evidence_usage(sections, pack),
                    "evidence_coverage": evidence_coverage_matrix(
                        plan.get("acts") or [], pack
                    ),
                    "budget_exhausted": budget_exhausted,
                    "evidence_fingerprint": evidence_fingerprint(pack),
                    "structure_lost_at": structure_lost_at,
                },
            )
        except Exception:
            case.status = previous_status
            db.commit()
            raise
        return row

    async def improve(
        self,
        db: Session,
        case: Case,
        story_version: StoryVersion,
        instruction: str,
    ) -> StoryVersion:
        """Apply an editorial instruction to an existing version and store a new one."""
        sources = db.query(Source).filter(Source.case_id == case.id).all()

        orig_words = len((story_version.story_text or "").split())
        tol = ai_config.master_generation.rewrite_length_tolerance
        prev_sections = stored_sections(story_version) or [
            {"id": "full", "text": story_version.story_text or ""}
        ]
        structured = is_structured(prev_sections)
        system = f"""
You are revising a long-form true-crime script.
Apply the requested editorial improvement while preserving factual accuracy.
Do not invent quotes, dialogue, evidence, motives, or scenes.
Do not add citations, URLs, headings, or source notes.
LENGTH CONTRACT: the source is {orig_words} words — output
{int(orig_words * (1 - tol))}–{int(orig_words * (1 + tol))} words.
Cut redundant material only where duplicated, and expand with grounded
detail when removing text would shrink the piece below the contract.
{_MARKER_INSTRUCTION if structured else ""}
Output only the rewritten story.
"""
        user = json.dumps(
            {
                "case": case.canonical_title,
                "instruction": instruction,
                "narrative_plan": story_version.narrative_angle,
                "story": (
                    _mark_sections(prev_sections) if structured
                    else story_version.story_text
                ),
            },
            ensure_ascii=False,
        )

        previous_status = case.status
        case.status = "writing"
        db.commit()
        try:
            with track_run(
                db, case.id, "Writer",
                input_summary=f"improve v{story_version.version}: {instruction[:150]}",
            ) as run:
                story_res = await self.gen.generate_text(self.roles["rewriter"], system, user)
                story = story_res.text
                stamp_run(run, story_res, self.roles["rewriter"])
                run.output_summary = f"words={len(story.split())}"

            sections, structure_kept = realign_sections(prev_sections, story)
            story = _join_sections(sections)
            new_words = len(story.split())
            if not (
                orig_words * (1 - tol)
                <= new_words
                <= orig_words * (1 + tol)
            ):
                # Step-15 length contract: a candidate that collapses or
                # balloons the draft is rejected, never stored.
                raise ValueError(
                    f"improve candidate violated length contract: "
                    f"{new_words} words vs contract "
                    f"{int(orig_words * (1 - tol))}–"
                    f"{int(orig_words * (1 + tol))}"
                )
            est_minutes = max(
                ai_config.story.min_target_minutes,
                round(new_words / ai_config.story.words_per_minute),
            )

            # A rewritten candidate must pass the same grounding and
            # consistency gates as a fresh generation — never stored blind.
            facts = db.query(Fact).filter(Fact.case_id == case.id).all()
            contradictions = (
                db.query(Contradiction)
                .filter(Contradiction.case_id == case.id)
                .all()
            )
            pack = build_evidence_pack(facts, contradictions, sources)
            grounding = await self._checked_grounding(db, case, story, pack)
            consistency = await self._checked_consistency(db, case, story, pack)

            # Critic always scores the exact text being stored.
            h = text_hash(story)
            with track_run(db, case.id, "Engagement Critic") as run:
                critique, res = await self.critic.critique(story, est_minutes)
                stamp_run(run, res, "engagement_critic", text_hash=h)
                run.output_summary = f"score={critique.get('score', 0)} hash={h[:8]}"

            gates = self._evaluate_gates(
                story, story_version.language, est_minutes,
                grounding, consistency,
                float(critique.get("score", 0)),
            )
            critique["consistency"] = consistency
            critique["grounding"] = {
                k: grounding.get(k)
                for k in ("grounding_score", "unsupported_claims",
                          "uncertainty_errors")
            }
            critique["quality_gates"] = gates
            critique["text_hash"] = h

            similarity, sim_status = self._score_similarity(db, case, story, sources)
            # Same phrase-level originality gate as fresh generation.
            from app.services.similarity import check_source_text_similarity
            src_sim = check_source_text_similarity(db, case.id, story)
            critique["source_text_similarity"] = src_sim
            if src_sim["status"] == "fail":
                gates["pass"] = False
                failures = gates.setdefault("failures", [])
                if "source_phrase_overlap" not in failures:
                    failures.append("source_phrase_overlap")
            try:
                plan = json.loads(story_version.narrative_angle or "")
            except (ValueError, TypeError):
                plan = story_version.narrative_angle
            row = self._new_version(
                db, case, plan,
                sections, story, float(critique.get("score", 0)),
                similarity, sim_status, critique,
                story_version.language,
                story_res,  # the model that wrote this version — never the critic
                kind=story_version.kind,
                master_version_id=story_version.master_version_id,
                derived_from_master_version=story_version.derived_from_master_version,
                extra_structure={
                    "evidence_fingerprint": evidence_fingerprint(pack),
                    "structure_lost_at": [] if structure_kept else ["improve"],
                },
            )
        except Exception:
            case.status = previous_status
            db.commit()
            raise
        return row

    # ------------------------------------------------------------------
    # stages
    # ------------------------------------------------------------------

    def _normalize_act_budgets(self, plan: dict, target_words: int) -> None:
        """Scale the director's per-act word budgets so they sum to the
        episode target and no act is starved below the minimum share."""
        acts = plan.get("acts") or []
        if not acts:
            return
        mg = ai_config.master_generation
        weights = [max(int(a.get("target_words") or 0), 0) for a in acts]
        total = sum(weights)
        if total <= 0:
            weights = [1] * len(acts)
            total = len(acts)
        min_words = int(target_words * mg.min_act_budget_share)
        scaled = [
            max(round(w / total * target_words), min_words) for w in weights
        ]
        # Fix rounding drift on the largest act.
        drift = target_words - sum(scaled)
        if drift:
            idx = max(range(len(scaled)), key=lambda i: scaled[i])
            scaled[idx] = max(min_words, scaled[idx] + drift)
        for a, w in zip(acts, scaled):
            a["target_words"] = int(w)

    async def _write_acts(
        self, db: Session, case: Case, pack: dict, plan: dict,
        acts: list[dict], language: str, tone: str,
    ) -> tuple[list[dict], GenerationResult]:
        """Generate each act separately against its budget and evidence,
        fitting each act's length before moving to the next."""
        sections: list[dict] = []
        last_res = GenerationResult(text="", model="", provider="")
        n = len(acts)
        narrated_ids: set[str] = set()
        for i, act in enumerate(acts):
            act.setdefault("id", f"act{i + 1}")
            later_reserved = sorted(
                set().union(
                    *(set(a.get("evidence_ids") or []) for a in acts[i + 1:])
                )
                | set(act.get("do_not_reveal") or [])
            )
            with track_run(
                db, case.id, "Writer",
                input_summary=(
                    f"act={act['id']} ({i + 1}/{n}) "
                    f"target≈{act.get('target_words')}w lang={language}"
                ),
            ) as run:
                prior = ""
                if sections:
                    tail = sections[-1]["text"].split()[-60:]
                    prior = " ".join(tail)
                text, last_res = await self.writer.write_act(
                    case, pack, plan, act, prior, later_reserved,
                    language, tone, role=self.roles["writer"],
                    already_narrated=sorted(narrated_ids),
                )
                stamp_run(run, last_res, self.roles["writer"])
                run.output_summary = f"words={len(text.split())}"
            text = strip_narration_artifacts(text)
            text, flog = await self._fit_section_length(
                db, case, act["id"], text, act.get("target_words"), pack,
                language,
            )
            revision_note = flog or {}
            sections.append(
                {"id": act["id"], "text": text, "meta": act,
                 "draft_log": revision_note}
            )
            # Everything this act may have narrated becomes established for
            # later acts — even if the director assigned overlapping ids.
            narrated_ids.update(act.get("evidence_ids") or [])
        return sections, last_res

    async def _fit_section_length(
        self, db: Session, case: Case, section_id: str, text: str,
        target_words: int | None, pack: dict, language: str,
    ) -> tuple[str, dict | None]:
        """Bring one section inside its word budget via expand/compress.

        Returns the (possibly unchanged) text plus a log entry if a repair
        ran. Best-effort for first drafts — the band is enforced on
        *revisions*, not on raw writer output.
        """
        mg = ai_config.master_generation
        if not target_words:
            return text, None
        log = None
        for attempt in range(ai_config.story.max_length_repair_iterations + 1):
            words = len(text.split())
            lo = target_words * (1 - mg.section_word_tolerance)
            hi = target_words * (1 + mg.section_word_tolerance)
            if lo <= words <= hi:
                break
            if attempt >= ai_config.story.max_length_repair_iterations:
                break
            if words < lo:
                instruction = (
                    f"This act is {words} words; its budget is ~{target_words}. "
                    "Expand it using ONLY evidence supplied below that the act "
                    "has not yet used — deepen scenes, transitions and "
                    "investigation detail. Never pad or repeat."
                )
            else:
                instruction = (
                    f"This act is {words} words; its budget is ~{target_words}. "
                    "Condense by removing repetition and low-value exposition "
                    "while keeping every evidence-backed fact."
                )
            system = f"""
You are a senior documentary editor repairing ONE act's length.
{instruction}
Use only details in the supplied evidence pack. Preserve facts,
uncertainty language and voice. Output only the act text.
"""
            user = json.dumps(
                {
                    "case": case.canonical_title,
                    "section_id": section_id,
                    "current_words": words,
                    "target_words": target_words,
                    "section_text": text,
                    "evidence": pack,
                },
                ensure_ascii=False,
            )
            with track_run(
                db, case.id, "Writer",
                input_summary=f"length fit: {section_id}",
            ) as run:
                res = await self.gen.generate_text(
                    self.roles["rewriter"], system, user
                )
                stamp_run(run, res, self.roles["rewriter"])
                run.output_summary = f"words={len(res.text.split())}"
            if res.text.strip():
                log = {
                    "stage": "length_fit",
                    "section_id": section_id,
                    "words_before": words,
                    "words_after": len(res.text.split()),
                }
                text = strip_narration_artifacts(res.text)
        return text, log

    @staticmethod
    def _pack_ids(pack: dict) -> set[str]:
        ids = set()
        for key in ("facts", "timeline", "contradictions", "approved_context"):
            ids.update(i["id"] for i in pack.get(key) or [])
        return ids

    @staticmethod
    def _grounding_fails(report: dict) -> bool:
        q = ai_config.story_quality
        return (
            float(report.get("grounding_score") or 0) < q.minimum_grounding_score
            or len(report.get("unsupported_claims") or [])
            > q.max_unsupported_claims
            or bool(report.get("uncertainty_errors"))
        )

    @staticmethod
    def _find_paragraph(paragraphs: list[str], span: str) -> int | None:
        if not span:
            return None
        needle = " ".join(span.split()).lower()
        marker = _marker_re()
        # [[ACT:id]] lines are structure, never repair targets.
        candidates = [
            (i, " ".join(p.split()).lower())
            for i, p in enumerate(paragraphs)
            if not marker.fullmatch(p.strip())
        ]
        for i, p in candidates:
            if needle in p:
                return i
        frag = needle[:60]
        for i, p in candidates:
            if frag in p:
                return i
        return None

    async def _repair_section_spans(
        self, db: Session, case: Case, section: dict, report: dict,
        pack: dict, language: str, limit: int | None = None,
    ) -> tuple[str, list[dict]]:
        """Surgical grounding repair: fix only the paragraphs containing
        unsupported claims, under an explicit length contract."""
        mg = ai_config.master_generation
        tol = mg.rewrite_length_tolerance
        text = section["text"]
        log: list[dict] = []
        cap = mg.max_span_repairs_per_pass
        if limit is not None:
            cap = min(cap, max(limit, 0))
        unsupported = (report.get("unsupported_claims") or [])[:cap]
        for claim_obj in unsupported:
            span = (
                claim_obj.get("exact_text_span")
                or claim_obj.get("location")
                or claim_obj.get("claim")
                or ""
            )
            paragraphs = text.split("\n\n")
            idx = self._find_paragraph(paragraphs, str(span))
            if idx is None:
                log.append(
                    {"section_id": section["id"], "repaired": False,
                     "reason": "span_not_located",
                     "claim": (claim_obj.get("claim") or "")[:120]}
                )
                continue
            para = paragraphs[idx]
            orig_words = len(para.split())
            near_ids = claim_obj.get("nearest_supported_evidence_ids") or []
            near = [
                f for key in ("facts", "contradictions", "timeline")
                for f in (pack.get(key) or [])
                if f.get("id") in near_ids
            ]
            system = f"""
You are a documentary fact editor repairing ONE paragraph.
The flagged claim below is not supported by the evidence pack.

Repair rules:
- Replace the unsupported specifics with the vaguer supported form from
  the supplied evidence (or delete just the unsupported fragment).
- Preserve the paragraph's purpose, narrative tension and all supported
  material. Do not summarize the paragraph.
- LENGTH CONTRACT: output {orig_words}±{int(orig_words * tol)} words.
- Narrate disputed items with uncertainty language.
Output only the repaired paragraph.
"""
            user = json.dumps(
                {
                    "paragraph": para,
                    "violation": {
                        "claim": claim_obj.get("claim"),
                        "reason": claim_obj.get("reason"),
                    },
                    "nearest_supported_evidence": (
                        near or pack.get("disputed_facts") or []
                    ),
                    "word_contract": {
                        "original": orig_words,
                        "min": int(orig_words * (1 - tol)),
                        "max": int(orig_words * (1 + tol)),
                    },
                },
                ensure_ascii=False,
            )
            try:
                with track_run(
                    db, case.id, "Writer",
                    input_summary=f"span repair: {section['id']}",
                ) as run:
                    res = await self.gen.generate_text(
                        self.roles["rewriter"], system, user
                    )
                    stamp_run(run, res, self.roles["rewriter"])
                    run.output_summary = f"words={len(res.text.split())}"
            except Exception:
                # A failed repair keeps the original paragraph — the
                # assembled grounding gate still judges honestly.
                log.append(
                    {"section_id": section["id"], "repaired": False,
                     "reason": "repair_call_failed",
                     "claim": (claim_obj.get("claim") or "")[:120]}
                )
                continue
            new_para = res.text.strip()
            if not (
                orig_words * (1 - tol)
                <= len(new_para.split())
                <= orig_words * (1 + tol)
            ):
                log.append(
                    {"section_id": section["id"], "rewrite_rejected": True,
                     "reason": "length_violation",
                     "words_before": orig_words,
                     "words_after": len(new_para.split())}
                )
                continue
            paragraphs[idx] = new_para
            text = "\n\n".join(paragraphs)
            log.append(
                {"section_id": section["id"], "repaired": True,
                 "words_before": orig_words, "words_after": len(new_para.split())}
            )
        return text, log

    def _spend_since(
        self, db: Session, case_id: int, since: datetime
    ) -> tuple[int, float]:
        tokens, cost = (
            db.query(
                func.coalesce(func.sum(AgentRun.total_tokens), 0),
                func.coalesce(func.sum(AgentRun.estimated_cost_usd), 0.0),
            )
            .filter(
                AgentRun.case_id == case_id,
                AgentRun.started_at >= since,
            )
            .one()
        )
        return int(tokens or 0), float(cost or 0.0)

    _EVIDENCE_NEEDED_OPS = {"ADD_HUMAN_DETAIL": "human_details"}

    def _evidence_bound_ops(
        self, ops: list[dict], section: dict, sections: list[dict], pack: dict
    ) -> tuple[list[dict], list[dict]]:
        """Filter critic ops: evidence-demanding ops get the evidence they
        may use, or are skipped with insufficient_evidence_for_requested_edit."""
        assigned_all: set[str] = set()
        for s in sections:
            assigned_all.update(s.get("meta", {}).get("evidence_ids") or [])
        unassigned = self._pack_ids(pack) - assigned_all
        scope = set(section.get("meta", {}).get("evidence_ids") or []) | unassigned

        applicable, skipped = [], []
        for op in ops:
            bucket = self._EVIDENCE_NEEDED_OPS.get(op.get("type"))
            if op.get("type") == "EXPAND":
                candidates = [
                    f["claim"] for f in pack.get("facts") or []
                    if f["id"] in scope
                ]
            elif bucket:
                candidates = [
                    f["claim"] for f in pack.get(bucket) or []
                    if f["id"] in scope
                ]
            else:
                applicable.append(op)
                continue
            if not candidates:
                skipped.append(
                    {"type": op.get("type"), "section_id": section["id"],
                     "insufficient_evidence_for_requested_edit": True}
                )
            else:
                op = dict(op)
                op["allowed_evidence"] = candidates
                applicable.append(op)
        return applicable, skipped

    async def _apply_revision_ops(
        self, db: Session, case: Case, sections: list[dict],
        section_scores: list[dict], pack: dict, plan: dict,
        critique: dict, language: str,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Apply the critic's structured operations to the weakest sections
        only, under per-section length contracts. Strong sections stay
        untouched."""
        mg = ai_config.master_generation
        q = ai_config.story_quality
        tol = mg.rewrite_length_tolerance
        ops_by_id = {
            s.get("section_id"): s.get("problems") or []
            for s in (critique.get("sections") or [])
            if s.get("section_id")
        }
        score_map = {
            s.get("_section_id"): float(s.get("score", 0) or 0)
            for s in section_scores
        }
        for cs in critique.get("sections") or []:
            sid = cs.get("section_id")
            if sid:
                score_map[sid] = min(
                    score_map.get(sid, 100), float(cs.get("score", 0) or 0)
                )
        weak = [
            i for i, s in enumerate(sections)
            if score_map.get(s["id"], 100) < q.section_engagement_threshold
            or ops_by_id.get(s["id"])
        ]
        weak.sort(key=lambda i: score_map.get(sections[i]["id"], 0))

        edits: list[dict] = []
        for i in weak[: mg.max_sections_per_revision_cycle]:
            section = sections[i]
            ops = ops_by_id.get(section["id"]) or []
            if not ops:
                probs = next(
                    (s.get("problems") or [] for s in section_scores
                     if s.get("_section_id") == section["id"]),
                    [],
                )
                ops = [
                    {"type": "REPHRASE", "instruction": p} for p in probs
                ]
            ops, skipped = self._evidence_bound_ops(ops, section, sections, pack)
            edits.extend(skipped)
            if not ops:
                continue
            orig_text = section["text"]
            orig_words = len(orig_text.split())
            act_pack = build_act_pack(
                pack, (section.get("meta") or {}).get("evidence_ids")
            )
            prev_tail = " ".join(
                sections[i - 1]["text"].split()[-40:]
            ) if i > 0 else ""
            next_head = " ".join(
                sections[i + 1]["text"].split()[:40]
            ) if i + 1 < len(sections) else ""
            system = f"""
You are a senior documentary editor revising ONE section of a true-crime
episode. Apply ONLY the listed editorial operations.

Rules:
- LENGTH CONTRACT: output {orig_words}±{int(orig_words * tol)} words.
- Use only details in the supplied evidence pack; ops marked with
  allowed_evidence may use only those items.
- Preserve every evidence-backed fact, all uncertainty language and the
  section's position in the story (it must flow from the previous
  section's ending and into the next section's opening).
- Never invent quotes, dialogue, scenes or details.
Output only this section's revised text.
"""
            user = json.dumps(
                {
                    "section_id": section["id"],
                    "operations": ops,
                    "section_text": orig_text,
                    "previous_section_ends_with": prev_tail,
                    "next_section_begins_with": next_head,
                    "evidence": act_pack,
                },
                ensure_ascii=False,
            )
            try:
                with track_run(
                    db, case.id, "Writer",
                    input_summary=f"targeted edit: {section['id']}",
                ) as run:
                    res = await self.gen.generate_text(
                        self.roles["rewriter"], system, user
                    )
                    stamp_run(run, res, self.roles["rewriter"])
                    run.output_summary = f"words={len(res.text.split())}"
            except Exception:
                edits.append(
                    {"section_id": section["id"], "rewrite_rejected": True,
                     "reason": "edit_call_failed"}
                )
                continue
            new_text = strip_narration_artifacts(res.text)
            if not (
                orig_words * (1 - tol)
                <= len(new_text.split())
                <= orig_words * (1 + tol)
            ):
                edits.append(
                    {"section_id": section["id"], "rewrite_rejected": True,
                     "reason": "length_violation", "words_before": orig_words,
                     "words_after": len(new_text.split())}
                )
                continue
            # Post-edit grounding: a worse-grounded section is reverted.
            rep = await self._checked_grounding(db, case, new_text, act_pack)
            if self._grounding_fails(rep):
                try:
                    repaired, rlog = await self._repair_section_spans(
                        db, case, {"id": section["id"], "text": new_text},
                        rep, act_pack, language,
                    )
                except Exception:
                    edits.append(
                        {"section_id": section["id"], "rewrite_rejected": True,
                         "reason": "repair_call_failed"}
                    )
                    continue
                edits.extend(rlog)
                rep2 = await self._checked_grounding(db, case, repaired, act_pack)
                if self._grounding_fails(rep2):
                    edits.append(
                        {"section_id": section["id"], "rewrite_rejected": True,
                         "reason": "grounding_regression"}
                    )
                    continue
                new_text = repaired
            sections[i] = dict(section, text=new_text)
            edits.append(
                {"section_id": section["id"], "accepted": True,
                 "words_before": orig_words,
                 "words_after": len(new_text.split())}
            )
        return sections, edits, section_scores

    async def _checked_grounding(
        self, db: Session, case: Case, text: str, pack: dict
    ) -> dict:
        """Assembled-stage grounding that fails CLOSED: a validator error
        must never silently clear the grounding gate."""
        try:
            return await self._grounding_check_only(db, case, text, pack)
        except Exception:
            return {
                "grounding_score": 0.0,
                "unsupported_claims": [
                    {"claim": "grounding_validator_error"}
                ],
                "uncertainty_errors": [],
                "validator_error": True,
            }

    async def _checked_consistency(
        self, db: Session, case: Case, text: str, pack: dict
    ) -> dict:
        try:
            return await self._consistency_check(db, case, text, pack)
        except Exception:
            return {
                "violations": [
                    {"severity": "high", "type": "other",
                     "detail": "consistency_checker_error"}
                ],
                "validator_error": True,
            }

    # Claim-first grounding triage (Part 19): deterministic extraction of
    # candidate factual tokens; only paragraphs carrying a token the
    # evidence pack cannot account for go to the LLM validator. Paragraphs
    # whose concrete specifics all appear in the pack never reach the
    # validator at all — cutting grounding cost sharply.
    _NUMERIC = re.compile(r"\b\d[\d,.:/-]*\d\b|\b\d\b")
    _ENTITY = re.compile(r"[A-Z][a-zA-Z'’\-]{3,}")
    _ENTITY_STOP = {
        "The", "But", "And", "For", "Nor", "Yet", "That", "This", "These",
        "Those", "When", "Where", "What", "Which", "Who", "Whom", "Whose",
        "Why", "How", "Then", "Than", "Thus", "There", "Their", "They",
        "Them", "He", "She", "His", "Her", "Its", "It", "We", "Our",
        "Us", "You", "Your", "I", "A", "An", "As", "At", "In", "Is", "On",
        "Of", "Or", "So", "To", "Up", "By", "My", "No", "Not", "Now",
        "Here", "Still", "Even", "Only", "Just", "Over", "Under", "After",
        "Before", "Because", "Until", "While", "Since", "Though", "If",
        "Perhaps", "Maybe", "Almost", "Always", "Never", "Ever", "Later",
        "Soon", "Today", "Tomorrow", "Yesterday", "English", "December",
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "Monday", "Tuesday",
        "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        "ACT", "Chapter", "Section",
    }

    @staticmethod
    def _pack_search_text(pack: dict) -> str:
        try:
            return json.dumps(pack, ensure_ascii=False).lower()
        except (TypeError, ValueError):
            return ""

    @classmethod
    def _suspect_paragraphs(cls, text: str, pack: dict) -> tuple[list[dict], int]:
        """Return ([{index, paragraph, unmatched_tokens}], total_paragraphs).

        A paragraph is suspect when it contains a number/date/entity that
        appears nowhere in the evidence pack — the classic signature of
        hallucinated specifics."""
        pack_text = cls._pack_search_text(pack)
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        suspects = []
        for i, para in enumerate(paragraphs):
            unmatched: list[str] = []
            for tok in cls._NUMERIC.findall(para):
                clean = tok.strip(",.")
                if clean and clean.lower() not in pack_text:
                    unmatched.append(tok)
            for tok in set(cls._ENTITY.findall(para)):
                if tok in cls._ENTITY_STOP or tok.lower() in pack_text:
                    continue
                unmatched.append(tok)
            if unmatched:
                suspects.append(
                    {"index": i, "paragraph": para, "unmatched": unmatched[:12]}
                )
        return suspects, len(paragraphs)

    async def _grounding_check_only(
        self, db: Session, case: Case, text: str, pack: dict
    ) -> dict:
        suspects, total = self._suspect_paragraphs(text, pack)
        if not suspects:
            # Deterministic pass: no paragraph contains a concrete specific
            # absent from the evidence pack — skip the LLM call entirely.
            return {
                "grounding_score": 1.0,
                "unsupported_claims": [],
                "uncertainty_errors": [],
                "method": "deterministic_scan",
                "paragraphs_checked": total,
            }
        checked_text = "\n\n".join(
            f"[P{s['index']}] {s['paragraph']}" for s in suspects
        )
        flagged = sorted({t for s in suspects for t in s["unmatched"]})
        system = """
You are a forensic fact-checker. Check every concrete claim in the story
(dates, numbers, names, physical details, quotes, weather, measurements)
against the evidence pack. A claim is "supported" only if the pack
contains it; reasonable narrative glue (transitions, mood, connective
tissue) is not a claim. Also flag any evidence-pack item marked
uncertain/disputed that the story presents as certain.

Return JSON only:
{
  "supported_claims": ["..."],
  "unsupported_claims": [
    {"claim": "...", "exact_text_span": "the exact story text containing the claim",
     "reason": "...",
     "nearest_supported_evidence_ids": ["F001"]}
  ],
  "uncertainty_errors": [
    {"claim": "...", "evidence_id": "F001", "reason": "disputed fact told as certain"}
  ],
  "grounding_score": 0.0-1.0
}
"""
        user = json.dumps(
            {
                "evidence": pack,
                # Claim-first triage: only paragraphs carrying tokens absent
                # from the pack are sent for deep validation (Part 19).
                "story": checked_text,
                "pre_flagged_tokens": flagged,
                "note": (
                    "Deterministic pre-scan selected these paragraphs because "
                    "they contain names/numbers/dates not found in the pack. "
                    "Validate them; non-suspect paragraphs were already "
                    "cleared by the deterministic check."
                ),
            },
            ensure_ascii=False,
        )
        with track_run(
            db, case.id, "Grounding Validator", input_summary=f"words={len(text.split())}"
        ) as run:
            data, res = await self.gen.generate_structured(
                "grounding_validator", system, user
            )
            stamp_run(run, res, "grounding_validator", text_hash=text_hash(text))
            run.output_summary = (
                f"score={data.get('grounding_score')} "
                f"unsupported={len(data.get('unsupported_claims') or [])} "
                f"uncertainty={len(data.get('uncertainty_errors') or [])} "
                f"suspect_paras={len(suspects)}/{total}"
            )
        data["method"] = "claim_first"
        data["paragraphs_checked"] = len(suspects)
        return data

    async def _consistency_check(
        self, db: Session, case: Case, text: str, pack: dict
    ) -> dict:
        """Catch rhetorical/metaphorical statements contradicting known facts."""
        system = """
You are a continuity editor for a documentary. Check the story against
the supplied canonical facts, timeline and contradictions. Look for:
- absolute claims contradicting the timeline
- metaphorical statements implying false facts ("extinguished forever"
  when the lamp was relit)
- wrong names, wrong attribution, wrong chronology
- disputed or uncertain claims narrated as established fact

Return JSON only:
{"violations": [{"type": "chronology|attribution|metaphor|certainty|other",
  "severity": "low|medium|high", "detail": "...", "location": "..."}]}
"""
        user = json.dumps(
            {
                "facts": pack["facts"],
                "timeline": pack["timeline"],
                "contradictions": pack["contradictions"],
                "story": text,
            },
            ensure_ascii=False,
        )
        with track_run(
            db, case.id, "Consistency Checker", input_summary=f"words={len(text.split())}"
        ) as run:
            data, res = await self.gen.generate_structured(
                "consistency_checker", system, user
            )
            stamp_run(run, res, "consistency_checker", text_hash=text_hash(text))
            viol = data.get("violations") or []
            high = sum(1 for v in viol if v.get("severity") == "high")
            run.output_summary = f"violations={len(viol)} high={high}"
        return data

    async def _section_pass(
        self, db: Session, case: Case, sections: list[dict],
        pack: dict, plan: dict, language: str,
    ) -> tuple[list[dict], list[dict]]:
        """Critique each section; targeted rewrite for the weakest ones."""
        q = ai_config.story_quality
        marker = q.section_marker_prefix
        scores: list[dict] = []
        for i, s in enumerate(sections):
            with track_run(
                db, case.id, "Section Critic",
                input_summary=f"section={s['id']}",
            ) as run:
                try:
                    data, res = await self.critic.critique_section(
                        s["id"], s["text"], f"{i + 1}/{len(sections)}"
                    )
                except Exception:
                    # An unscored section is simply not targeted for edits.
                    run.output_summary = "critique_failed"
                    continue
                stamp_run(run, res, "section_critic")
                run.output_summary = f"score={data.get('score', 0)}"
            data["_section_id"] = s["id"]
            scores.append(data)

        # Rewrite the weakest sections first — a good opening must not
        # hide a weak middle. Strong sections are never touched.
        weak = sorted(
            (
                (i, s) for i, s in enumerate(scores)
                if float(s.get("score", 0)) < q.section_engagement_threshold
            ),
            key=lambda t: float(t[1].get("score", 0)),
        )
        tol = ai_config.master_generation.rewrite_length_tolerance
        for i, s in weak[: q.max_section_rewrites]:
            orig = sections[i]
            orig_words = len(orig["text"].split())
            system = f"""
You are revising ONE section of a documentary script. Apply the editor
feedback while preserving all evidence-backed facts and matching the
surrounding narration's voice.
LENGTH CONTRACT: output {orig_words}±{int(orig_words * tol)} words —
expand with unused evidence or deepen existing detail, do not compress.
Use only details in the evidence pack. Output only this section's text —
no markers, no headings, no commentary.
"""
            user = json.dumps(
                {
                    "section_id": orig["id"],
                    "editor_feedback": s.get("problems") or [],
                    "section_text": orig["text"],
                    "evidence": pack,
                },
                ensure_ascii=False,
            )
            with track_run(
                db, case.id, "Writer",
                input_summary=f"targeted section rewrite: {orig['id']}",
            ) as run:
                try:
                    res = await self.gen.generate_text(
                        self.roles["rewriter"], system, user
                    )
                except Exception:
                    # A failed rewrite must not kill the run — keep the
                    # original section text.
                    run.output_summary = f"section={orig['id']} rewrite_failed"
                    continue
                stamp_run(run, res, self.roles["rewriter"])
                run.output_summary = f"section={orig['id']} words={len(res.text.split())}"
            if res.text.strip() and (
                orig_words * (1 - tol)
                <= len(res.text.split())
                <= orig_words * (1 + tol)
            ):
                sections[i] = {
                    "id": orig["id"], "text": res.text.strip(),
                    "meta": orig.get("meta") or {},
                }
        return sections, scores

    async def _final_edit(
        self, db: Session, case: Case, story: str, language: str,
        marked: bool = False,
    ) -> tuple[str | None, GenerationResult | None]:
        """Last-pass copyedit through the premium final_editor role.
        With marked=True the story carries [[ACT:id]] lines that must
        survive the edit."""
        system = f"""
You are the final editor of a documentary script.
Polish the story in its existing language ({language}) without rewriting it.
Remove awkward phrasing, repetition and meta-commentary; improve transitions
and clarity; remove any leftover model artifacts, markdown headings or
separator lines. Preserve all verified facts, uncertainty, tone, structure
and approximate length. Do not add or remove substantive content.
{_MARKER_INSTRUCTION if marked else ""}
Output only the story text.
"""
        user = json.dumps(
            {"language": language, "story": story}, ensure_ascii=False
        )
        with track_run(db, case.id, "Final Editor") as run:
            res = await self.gen.generate_text(self.roles["final_editor"], system, user)
            stamp_run(run, res, self.roles["final_editor"], text_hash=text_hash(res.text))
            run.output_summary = f"words={len(res.text.split())}"
        return res.text, res

    # ------------------------------------------------------------------
    # gates / scoring / persistence
    # ------------------------------------------------------------------

    def _evaluate_gates(
        self,
        story: str,
        language: str,
        target_minutes: int,
        grounding: dict | None = None,
        consistency: dict | None = None,
        engagement: float | None = None,
        wpm: int | None = None,
    ) -> dict:
        """Deterministic + validator quality gates. Ready only if all pass."""
        sq = ai_config.story_quality
        failures = []
        target_words = target_minutes * (wpm or ai_config.story.words_per_minute)
        words = len(story.split())
        if not (
            target_words * ai_config.story.minimum_length_ratio
            <= words
            <= target_words * ai_config.story.maximum_length_ratio
        ):
            failures.append("length_out_of_range")

        lang = language_quality(
            story, language, ai_config.language_quality_for(language)
        )
        if not lang["pass"]:
            failures.append("language_quality")

        if re.search(r"https?://|www\.", story):
            failures.append("output_purity")
        if ai_config.story_quality.strip_markdown_artifacts and re.search(
            r"^\s*#{1,6}\s|^\s*[-*_]{3,}\s*$", story, re.M
        ):
            failures.append("markdown_artifacts")

        grounding_score = None
        if grounding is not None:
            grounding_score = float(grounding.get("grounding_score") or 0)
            unsupported = len(grounding.get("unsupported_claims") or [])
            uncertain_errs = len(grounding.get("uncertainty_errors") or [])
            if (
                grounding_score < sq.minimum_grounding_score
                or unsupported > sq.max_unsupported_claims
                or uncertain_errs
            ):
                failures.append("grounding")

        high_violations = 0
        if consistency is not None:
            high_violations = sum(
                1
                for v in (consistency.get("violations") or [])
                if v.get("severity") == "high"
            )
            if high_violations > sq.consistency_max_high_severity:
                failures.append("consistency")

        if (
            engagement is not None
            and engagement < ai_config.story.engagement_threshold
        ):
            failures.append("engagement_below_threshold")

        failures = list(dict.fromkeys(failures))  # dedupe, keep order
        return {
            "pass": not failures,
            "failures": failures,
            "words": words,
            "target_words": target_words,
            "language_quality": lang,
            "grounding_score": grounding_score,
            "high_consistency_violations": high_violations,
        }

    def _score_similarity(
        self, db: Session, case: Case, story: str, sources: list[Source]
    ) -> tuple[float | None, str]:
        source_texts = [s.raw_text for s in sources if s.is_authorized_text and s.raw_text]
        with track_run(db, case.id, "Similarity Critic") as run:
            if not source_texts:
                run.output_summary = "similarity not evaluated: no authorized source text"
                return None, "not_evaluated"
            similarity = SimilarityCritic.rough_similarity(story, source_texts)
            run.output_summary = f"similarity={similarity:.2f} texts={len(source_texts)}"
        return similarity, "evaluated"

    def _new_version(
        self, db: Session, case: Case, plan, sections: list[dict],
        story: str, engagement: float,
        similarity: float | None, similarity_status: str,
        critique: dict, language: str,
        generation: GenerationResult | None = None,
        kind: str = "direct",
        master_version_id: int | None = None,
        derived_from_master_version: int | None = None,
        native_quality_score: float | None = None,
        semantic_consistency_score: float | None = None,
        factual_consistency_score: float | None = None,
        extra_structure: dict | None = None,
    ) -> StoryVersion:
        current_versions = (
            db.query(StoryVersion)
            .filter(StoryVersion.case_id == case.id)
            .count()
        )
        gates = critique.get("quality_gates") or {}
        status = "ready" if gates.get("pass", True) else "needs_revision"

        # A new master version ages out every localization derived from an
        # earlier master — traceability, never silent replacement.
        if kind == "master":
            db.query(StoryVersion).filter(
                StoryVersion.case_id == case.id,
                StoryVersion.kind == "localized",
                StoryVersion.status.in_(["draft", "needs_revision", "ready"]),
            ).update({"status": "outdated"})

        # Best version = highest engagement within the same kind+language
        # family (masters vs Persian localizations vs direct versions do
        # not compete with each other).
        best_score = (
            db.query(StoryVersion.engagement_score)
            .filter(
                StoryVersion.case_id == case.id,
                StoryVersion.kind == kind,
                StoryVersion.language == language,
            )
            .order_by(StoryVersion.engagement_score.desc())
            .first()
        )
        is_best = not best_score or engagement > best_score[0]
        if is_best:
            db.query(StoryVersion).filter(
                StoryVersion.case_id == case.id,
                StoryVersion.kind == kind,
                StoryVersion.language == language,
            ).update({"is_best": False})

        # Internal structure kept separate from narration text. Section
        # TEXT is stored too: voice blocks and per-language timelines need
        # to know which words belong to which act.
        stored = clean_sections(sections)
        sections_match = _norm_ws(
            "\n\n".join(s["text"] for s in stored)
        ) == _norm_ws(story)
        if not sections_match:
            # Never store act text that differs from the approved story —
            # fall back to the story itself as one section.
            stored = [{"id": "full", "text": story}]
        structure = {
            "title": plan.get("title") if isinstance(plan, dict) else None,
            "central_question": plan.get("central_question")
            if isinstance(plan, dict)
            else None,
            "acts": plan.get("acts") if isinstance(plan, dict) else None,
            "sections": [
                {"id": s["id"], "words": len(s["text"].split()), "text": s["text"]}
                for s in stored
            ],
            "structured": is_structured(stored),
        }
        if extra_structure:
            structure.update(extra_structure)
        if not sections_match and is_structured(sections):
            structure["structure_lost_at"] = list(
                structure.get("structure_lost_at") or []
            ) + ["save_text_mismatch"]

        row = StoryVersion(
            case_id=case.id,
            version=current_versions + 1,
            narrative_angle=(
                json.dumps(plan, ensure_ascii=False)
                if isinstance(plan, dict)
                else str(plan)
            ),
            narrative_structure=json.dumps(structure, ensure_ascii=False),
            language=language,
            kind=kind,
            master_version_id=master_version_id,
            derived_from_master_version=derived_from_master_version,
            native_quality_score=native_quality_score,
            semantic_consistency_score=semantic_consistency_score,
            factual_consistency_score=factual_consistency_score,
            story_text=story,
            text_hash=text_hash(story),
            engagement_score=engagement,
            similarity_score=similarity,
            similarity_status=similarity_status,
            status=status,
            is_best=is_best,
            critic_notes=json.dumps(critique, ensure_ascii=False),
            generation_provider=generation.provider if generation else None,
            generation_model=generation.model if generation else None,
        )
        db.add(row)
        case.status = "story_ready"
        db.commit()
        db.refresh(row)
        return row

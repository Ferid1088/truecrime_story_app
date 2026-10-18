import hashlib
import json
import re

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt, prompt_sha256
from app.db.models import (
    Case,
    Contradiction,
    Fact,
    Source,
    StoryVersion,
)
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationResult
from app.services.evidence_selection import EvidenceSelector
from app.utils import (
    strip_narration_artifacts,
    text_hash,
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
    so later stages (the language texts, voice, visuals) can tell whether the
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


def _fact_item(f: Fact, number: int, q) -> dict:
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
    return {
        "id": f"F{number:03d}",
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
        # Part 3(e) item 2: "keep the single-source flag for facts
        # only one [source] claims, and show it."
        "single_source": len(source_ids) == 1,
    }


def build_evidence_pack(
    facts: list[Fact], contradictions: list[Contradiction], sources: list[Source],
    extra_facts: int | None = 0,
) -> dict:
    """The evidence pack. The strongest `max_evidence_per_story` facts get the ids
    F001.. (the ids a story plan refers to). `extra_facts` adds the facts the cap left
    out AFTER them, strongest first, without moving any id: 0 adds none, a number adds
    at most that many, None adds all (the long draft reads everything)."""
    q = ai_config.story_quality
    contradictions = _by_id(list(contradictions))
    sources = _by_id(list(sources))
    all_facts = _by_id(list(facts))
    selector = EvidenceSelector(q.max_evidence_per_story, sources, contradictions)
    facts, dropped_facts = selector.select(all_facts)
    kept = {id(f) for f in facts}
    extras = sorted((f for f in all_facts if id(f) not in kept), key=selector._rank_key)
    if extra_facts is not None:
        extras = extras[:extra_facts]
    fact_items = [_fact_item(f, i, q) for i, f in enumerate(facts, 1)]
    fact_items += [_fact_item(f, i, q) for i, f in enumerate(extras, len(fact_items) + 1)]
    dropped_facts = [d for d in dropped_facts
                     if d["fact_id"] not in {f.id for f in extras}]
    kept_count = len(facts)
    facts = [*facts, *extras]
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
        # Part 2(c): the evidence diet never deletes a Fact row, but caps
        # what any one pack includes — every excluded fact is named here
        # with its reason, so "fewer, better items" stays auditable rather
        # than a silent cut.
        "evidence_diet": {
            "cap": q.max_evidence_per_story,
            "kept": kept_count,
            "added_beyond_cap": len(extras),
            "dropped": dropped_facts,
        },
    }


_DIRECTOR_CLAIM_CHARS = 220
_DIRECTOR_FACT_KEYS = ("id", "category", "narrative_value", "evidence_strength", "uncertain",
                       "people")
_PACK_ID_LISTS = ("disputed_facts", "human_details", "scene_details", "investigation_details",
                  "physical_evidence", "historical_context", "environment_details",
                  "approved_quotes")


def director_view(pack: dict) -> dict:
    """The evidence as the story director plans with it: one short line per
    item and the narrative buckets as id lists. Original wording, supporting
    texts and source ids stay in the full pack, which the act writers draw
    from by id — a case with thousands of facts would otherwise overrun the
    model's prompt limit (1.18M tokens against 1M)."""
    view = {
        "facts": [
            {**{k: f.get(k) for k in _DIRECTOR_FACT_KEYS},
             "claim": (f.get("claim") or "")[:_DIRECTOR_CLAIM_CHARS]}
            for f in pack.get("facts") or []
        ],
        "timeline": [{"id": t["id"], "event_date": t["event_date"]}
                     for t in pack.get("timeline") or []],
        "contradictions": pack.get("contradictions") or [],
        "approved_context": [{**c, "summary": c["summary"][:300]}
                             for c in pack.get("approved_context") or []],
        "research_gaps": pack.get("research_gaps") or [],
    }
    for bucket in _PACK_ID_LISTS:
        view[bucket] = [f["id"] for f in pack.get(bucket) or []]
    return view








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
# repairs, polish passes and rewrites.
# ---------------------------------------------------------------------------

_MARKER_INSTRUCTION = (
    prompt("agents/story/marker_instruction")
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
    """Map an edited or rewritten text back onto the previous
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
        context: dict | None = None,
    ) -> tuple[dict, GenerationResult]:
        system = prompt("agents/story/design")
        user = json.dumps(
            {
                "case": case.canonical_title,
                "target_minutes": target_minutes,
                "language": language,
                "editorial_context": context or {},
                "max_evidence_ids_per_act": ai_config.story_quality.max_evidence_per_act,
                "evidence": director_view(pack),
            },
            ensure_ascii=False,
            default=str,
        )
        data, res = await run_agent("story.design", self.gen, user, system=system, role=role)
        return data, res


_OPENING_PREFERENCE = {
    "SOLVED": ["courtroom_outcome", "critical_moment", "victim_introduction",
               "evidence_discovery", "last_sighting", "emergency_call"],
    "UNSOLVED": ["unanswered_question", "last_sighting", "mysterious_statement",
                 "timeline_anomaly", "important_location"],
}


def editorial_context(db: Session, case: Case, follow_up: dict | None = None) -> dict:
    """What the story director must know beyond the evidence: where the
    case stands, the opening strategies and the ones recent films used,
    and — for an update video — the earlier coverage."""
    from app.documentary.openings import recent_openings, strategies

    ctx = {
        "case_status": case.resolution_status or "UNKNOWN",
        "status_summary": case.resolution_summary,
        "opening_strategies": strategies(),
        "avoid_openings": recent_openings(db, exclude_case_id=case.id),
    }
    if follow_up:
        ctx["follow_up"] = {k: follow_up.get(k) for k in (
            "intro", "original_title", "episode_number", "original_published",
            "previous_status", "current_status", "development")}
    return ctx


def choose_opening(plan: dict, context: dict) -> tuple[str, str]:
    """(strategy, reason): the director's choice when it is a known
    strategy, else a deterministic fallback by case status that avoids
    the recent films' openings."""
    from app.documentary.openings import normalize_strategy

    if context.get("follow_up"):
        return (normalize_strategy(None, follow_up=True),
                "follow-up film: opens with the earlier coverage")
    chosen = normalize_strategy(plan.get("opening_strategy") if isinstance(plan, dict) else None)
    if chosen:
        return chosen, str((plan or {}).get("opening_reason") or "chosen by the story director")
    avoid = set(context.get("avoid_openings") or [])
    prefs = _OPENING_PREFERENCE.get(context.get("case_status"),
                                    ["critical_moment", "victim_introduction",
                                     "important_location", "unanswered_question"])
    names = [p for p in prefs if p in context.get("opening_strategies", {})]
    pick = next((p for p in names if p not in avoid), names[0] if names else "critical_moment")
    return pick, "fallback: the director named no known strategy; first fitting one not used " \
                 "by the most recent films"


def normalize_act_budgets(plan: dict, target_words: int) -> None:
    """Scale the director's per-act word budgets so they sum to the
    episode target and no act is starved below the minimum share."""
    acts = plan.get("acts") or []
    if not acts:
        return
    sp = ai_config.story_plan
    weights = [max(int(a.get("target_words") or 0), 0) for a in acts]
    total = sum(weights)
    if total <= 0:
        weights = [1] * len(acts)
        total = len(acts)
    min_words = int(target_words * sp.min_act_budget_share)
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


def save_story_version(
    db: Session, case: Case, plan, sections: list[dict],
    story: str, engagement: float,
    similarity: float | None, similarity_status: str,
    critique: dict, language: str,
    generation: GenerationResult | None = None,
    kind: str = "direct",
    master_version_id: int | None = None,
    native_quality_score: float | None = None,
    semantic_consistency_score: float | None = None,
    factual_consistency_score: float | None = None,
    extra_structure: dict | None = None,
    writer_prompt: str | None = None,
    writer_prompt_sha256: str | None = None,
) -> StoryVersion:
    current_versions = (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case.id)
        .count()
    )
    gates = critique.get("quality_gates") or {}
    status = "ready" if gates.get("pass", True) else "needs_revision"

    # Best version = highest engagement within the same kind+language
    # family (spoken versions of different languages do not compete).
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
        "opening_strategy": plan.get("opening_strategy") if isinstance(plan, dict) else None,
        "opening_reason": plan.get("opening_reason") if isinstance(plan, dict) else None,
        "case_status": plan.get("case_status") if isinstance(plan, dict) else None,
        "follow_up": plan.get("follow_up") if isinstance(plan, dict) else None,
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
        writer_prompt_name=writer_prompt,
        writer_prompt_sha256=(writer_prompt_sha256
                              or (prompt_sha256(writer_prompt) if writer_prompt else None)),
    )
    db.add(row)
    case.status = "story_ready"
    db.commit()
    db.refresh(row)
    return row

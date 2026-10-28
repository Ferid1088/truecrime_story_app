"""The Detective Director's tool catalog (Section C input `tool_catalog`).

PROVISIONAL: Section D ("Make all twenty alternatives executable detective tools") builds the
real common tool descriptor/adapter with live availability, typed input schemas and asset
requirements derived from the actual registry. Until that lands, this module gives the director
the same 22 tools/engines/paths that Section A's audit traced in
`docs/detective_toolkit_capability_matrix.md`, with the investigative-job wording from Section D's
own tool table in the implementation prompt. Availability here is static ("reachable" per Section
A), not live-checked against the current case's assets — Section D must replace that.
"""

from __future__ import annotations

# tool_id -> (purpose, input_contract_summary, availability)
# tool_id matches app/documentary/graphics/registry.py `kind` where a graphic kind exists, the
# cinematic-engine `kind` otherwise, or "photo"/"host" for the two non-graphic tools.
TOOL_CATALOG: dict[str, dict[str, str]] = {
    "map": {
        "purpose": "Orient the viewer geographically and clarify spatial relationships.",
        "input_contract": "place name + region string; camera curve from the zoom_map subsystem.",
        "availability": "reachable",
    },
    "detective_board": {
        "purpose": "Connect people, objects, events, claims, and questions through time.",
        "input_contract": "blueprint beats (reveals/opens/answers/purpose/human_focus); localized titles.",
        "availability": "reachable",
    },
    "recap": {
        "purpose": "Orient the viewer in what is established, disputed, and still open.",
        "input_contract": "confirmed/disputed/unknown fact-id groups, cadence.",
        "availability": "reachable",
    },
    "unknowns": {
        "purpose": "Show partial knowledge and the precise question that remains.",
        "input_contract": "known/unknown/disputed statement list, basis + fact_id per item.",
        "availability": "reachable",
    },
    "conflict": {
        "purpose": "Isolate the specific disagreement between accounts.",
        "input_contract": "one structured contradiction (sides, topic, description).",
        "availability": "reachable",
    },
    "conflict_board": {
        "purpose": "Relate several already-introduced conflicts.",
        "input_contract": "cards = conflict pages already shown this round.",
        "availability": "reachable",
    },
    "timeline": {
        "purpose": "Explain event order and chronological relationships.",
        "input_contract": "dated events (fact_id, date, label, claim), deduped per event.",
        "availability": "reachable (date-sort bug tracked separately, see memory bug_case_timeline_date_order)",
    },
    "investigation_clock": {
        "purpose": "Compare the crime's timeline with investigative progress or delays.",
        "input_contract": "crime/investigation track events, each a dated timeline id.",
        "availability": "reachable",
    },
    "fact_card": {
        "purpose": "Emphasize a consequential fact.",
        "input_contract": "1-N confirmed-fact short sentences, grouped per beat.",
        "availability": "reachable (currently a last-resort filler; Section D must let it be chosen directly)",
    },
    "key_facts": {
        "purpose": "Make important figures, dates, and times easy to grasp.",
        "input_contract": "extracted figure/value + caption line per confirmed fact.",
        "availability": "reachable",
    },
    "document": {
        "purpose": "Direct attention to relevant words in a document.",
        "input_contract": "document id/type/issuer/date + verified verbatim quote span.",
        "availability": "reachable (narrow: verbatim + official-source quotes only)",
    },
    "quote": {
        "purpose": "Preserve and emphasize a speaker's exact words.",
        "input_contract": "fullest verified quote text + optional speaker, fact_id.",
        "availability": "reachable",
    },
    "headline": {
        "purpose": "Show the reported/public framing when the narration discusses it.",
        "input_contract": "parsed source headline/date/publisher + placement beat window.",
        "availability": "reachable",
    },
    "floor_plan": {
        "purpose": "Clarify rooms, entrances, and spatial relationships from available plan data.",
        "input_contract": "rooms/entrances/legend.",
        "availability": "dead — registered renderer, no production caller builds this offer "
                         "(Section A finding); Section D must either wire a caller or report it unavailable",
    },
    "evidence_check": {
        "purpose": "Explain what an item establishes and what remains unsupported.",
        "input_contract": "item + establishes/not_establishes/supported_by/contradicted_by/assessment.",
        "availability": "reachable",
    },
    "custody": {
        "purpose": "Follow an evidence item's documented movement and handling.",
        "input_contract": "item + 2-5 documented stages, optional gap_after.",
        "availability": "reachable",
    },
    "forensic": {
        "purpose": "Explain a sample, method, result, and limitations.",
        "input_contract": "exam type, sample/method/result/limits statements.",
        "availability": "reachable",
    },
    "interrogation": {
        "purpose": "Compare questions, answers, and changes in statements.",
        "input_contract": "1-6 question/answer turns, optional earlier/later compare.",
        "availability": "reachable",
    },
    "courtroom": {
        "purpose": "Connect the competing arguments, evidence, and court finding.",
        "input_contract": "issue + prosecution/defense statements + finding.",
        "availability": "reachable",
    },
    "sentencing": {
        "purpose": "Explain charges, outcomes, and the sentence.",
        "input_contract": "charges, aggravating/mitigating, arrangement, final sentence.",
        "availability": "reachable",
    },
    "photo": {
        "purpose": "Show the real person, place, object, or moment the narration describes.",
        "input_contract": "approved VisualAsset + role/entity + clip bounds.",
        "availability": "reachable (primary fill path today; Section D must let the director choose it "
                         "deliberately rather than it always filling first)",
    },
    "host": {
        "purpose": "A presenter moment: human connection, transition, or framing in the host's own voice.",
        "input_contract": "planned HostScene row, produced independently of this plan.",
        "availability": "reachable, but host placement is independent of the detective plan today "
                         "(Section H integration gap)",
    },
}


def build_tool_catalog() -> list[dict[str, str]]:
    """The `tool_catalog` entries for the Detective Director's input payload."""
    return [{"tool_id": tool_id, **info} for tool_id, info in TOOL_CATALOG.items()]

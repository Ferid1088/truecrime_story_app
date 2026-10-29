"""The Detective Director's tool catalog (the `tool_catalog` of its input): the 21 tools it may choose -
the 20 named tools plus "photo" - with the investigative job of each from the implementation prompt's tool
table and `dispatch.BUILDERS` as the real execution path behind them. The availability text is static: it states
what each tool needs, not what the current case happens to hold (that is the Main Visual Planner's check at
dispatch). Host is deliberately excluded; see the comment above the end of TOOL_CATALOG.
"""

from __future__ import annotations

# tool_id -> (purpose, input_contract_summary, availability)
# tool_id matches app/documentary/graphics/registry.py `kind` where a graphic kind exists, the
# cinematic-engine `kind` otherwise, or "photo" for the one non-graphic, non-host tool.
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
        "availability": "reachable; when you choose it, it gets its own moment in the beat",
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
        "availability": "reachable only when the action's payload carries real rooms and markers from stored "
                         "plan-document data (a plan is never invented from the narration); otherwise choose "
                         "another tool",
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
        "availability": "reachable; choose it when a real picture of the person, place or object serves the "
                         "aim better than a graphic, and as a pause between text-heavy cards. Only pictures "
                         "listed in asset_catalog exist",
    },
    # Deliberately NOT a catalog entry: the Host system is its own authority, owned by the Main
    # Visual Planner, not by the detective toolkit (owner correction, 2026-10-28; see
    # docs/MAIN_VISUAL_PLANNER_12_STEP_IMPLEMENTATION.md Step 4 and
    # docs/detective_toolkit_section_cd_architecture_remediation.md). The Detective Director must
    # never choose, place, or request a host appearance as one of its tool actions - Host plans,
    # writes, and times its own scenes independently, and the detective plans the surrounding case
    # visuals around them. Excluding "host" from TOOL_CATALOG means `known_tool_ids` in
    # schema.py:validate_director_output never contains it, so a director output that still
    # proposes tool_id="host" (the runtime prompt's prose generically mentions "host shots" as a
    # useful tool, per the doc's own required wording) is rejected as an unknown tool_id, the same
    # as any other invalid tool_id - not silently dispatched. Passing the Host system's already-
    # placed scenes to the director as read-only context (so it can plan around them rather than
    # compete with them) is real, deferred work for Section H's production-integration pass, not
    # this fix - `director.py`'s `asset_catalog` is still a stub (`[]`) pending that.
}


def build_tool_catalog() -> list[dict[str, str]]:
    """The `tool_catalog` entries for the Detective Director's input payload. Each entry also says exactly what the
    card needs bound to it (the claim attributes, relation types and questions its builder reads), the contract the
    toolkit checks every action against, so the Director does not have to find it out from a failed repair."""
    from app.documentary.detective.buildable import NEEDS

    return [{"tool_id": tool_id, **info, **({"how_to_bind": NEEDS[tool_id]} if tool_id in NEEDS else {})}
            for tool_id, info in TOOL_CATALOG.items()]

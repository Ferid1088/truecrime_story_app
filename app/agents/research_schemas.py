"""Part 2(b): one pydantic schema per research/extraction stage.

Scope decision (recorded in ROADMAP.md): these three schemas validate the
STRUCTURE of each stage's output (required fields present, right types,
well-formed nested objects) — not the open-ended categorical fields
(narrative_value, evidence_strength, quote_status, category) the prompts
describe as enums but the rest of the codebase already treats as free
strings (app.agents.research._store_facts defaults an unrecognized
category to "general" rather than rejecting it; app.agents.story reads
Fact.category as plain text). Enforcing those as strict enums here would
turn ordinary model drift on a non-critical field into a paid repair call
for no real correctness gain. A missing claim, a non-numeric confidence or
a malformed supporting_span, on the other hand, would corrupt storage or
crash downstream code — those are enforced.

Real-gateway finding behind this design (see ROADMAP.md Part 2(b), which
names the real provider/model this was checked against — deliberately not
repeated here per this project's "no hardcoded model ids/provider names in
app/" rule, tests/test_generation.py and tests/test_provider_split.py):
the configured generation gateway accepts `response_format: json_schema`/
`json_object` for the research-pipeline's configured model without error,
but does not actually enforce it (the response is byte-identical to no
response_format at all, including stray markdown fences). Forced
tool-calling (`tools` + `tool_choice`) DOES genuinely constrain the
output — verified live — but adopting it as the transport for these three
stages is a separate, larger change (it would touch
app.providers.generation, shared by all 18 roles); these schemas are the
validate-and-repair safety net that works regardless of transport, and are
needed either way since even genuine tool-calling enforcement from an
Anthropic-backed gateway is not the same guarantee as a hand-written
pydantic model (e.g. it won't catch an out-of-range confidence).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SupportingSpan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chunk_id: int
    start: int
    end: int


class FactItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim: str = Field(min_length=1)
    category: str = "general"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_ids: list[int] = []
    disputed: bool = False
    event_date: str | None = None
    original_claim: str | None = None
    original_language: str = "en"
    narrative_value: str | None = None
    evidence_strength: str | None = None
    supporting_span: SupportingSpan | None = None
    chunk_ids: list[int] = []
    quote_status: str | None = None
    speaker: str | None = None
    people: list[str] = []
    locations: list[str] = []
    # Older/test-provided shape (Part 2(e) item 4's backward-compat path):
    # a fact may already carry plain supporting_text instead of a span.
    supporting_text: str | None = None


class FactExtractorOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facts: list[FactItem] = []


class TimelineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    event_date: str | None = None


class TimelineBuilderOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timeline: list[TimelineItem] = []


class ContradictionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_ids: list[int] = []
    severity: str = "medium"


class ContradictionAnalyzerOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contradictions: list[ContradictionItem] = []

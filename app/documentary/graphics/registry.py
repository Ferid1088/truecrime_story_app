"""The graphic types the planner can offer, by kind."""

from __future__ import annotations

from app.documentary.graphics.base import Graphic, GraphicUnavailable
from app.documentary.graphics.conflict import ConflictBoard, ConflictPage
from app.documentary.graphics.document import DocumentCard
from app.documentary.graphics.engines.evidence import (
    ChainOfCustody,
    ForensicExamination,
    MapOfUnknowns,
)
from app.documentary.graphics.engines.story import InterrogationAnalysis, InvestigationClock
from app.documentary.graphics.engines.trial import (
    CourtroomReasoning,
    EvidenceCheck,
    SentencingExplanation,
)
from app.documentary.graphics.floorplan import FloorPlan
from app.documentary.graphics.headline import HeadlineCard
from app.documentary.graphics.keyfacts import KeyFacts
from app.documentary.graphics.quote import QuoteCard
from app.documentary.graphics.recap import RecapBoard
from app.documentary.graphics.timeline import Timeline

GRAPHICS: dict[str, Graphic] = {g.kind: g for g in (
    DocumentCard(), ConflictPage(), ConflictBoard(), Timeline(), RecapBoard(), FloorPlan(), KeyFacts(),
    HeadlineCard(), QuoteCard(), CourtroomReasoning(), ChainOfCustody(), MapOfUnknowns(), ForensicExamination(),
    InterrogationAnalysis(), SentencingExplanation(), InvestigationClock(), EvidenceCheck())}


def graphic(kind: str) -> Graphic:
    try:
        return GRAPHICS[kind]
    except KeyError:
        raise GraphicUnavailable(f"unknown graphic type {kind!r}") from None

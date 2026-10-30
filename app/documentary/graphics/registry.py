"""The graphic types the planner can offer, by kind."""

from __future__ import annotations

from app.documentary.graphics.base import Graphic, GraphicUnavailable
from app.documentary.graphics.conflict import ConflictPage
from app.documentary.graphics.document import DocumentCard
from app.documentary.graphics.engines.evidence import ForensicExamination
from app.documentary.graphics.engines.trial import EvidenceCheck
from app.documentary.graphics.factcard import FactCard
from app.documentary.graphics.headline import HeadlineCard
from app.documentary.graphics.quote import QuoteCard
from app.documentary.graphics.recap import RecapBoard
from app.documentary.graphics.timeline import Timeline

GRAPHICS: dict[str, Graphic] = {g.kind: g for g in (
    DocumentCard(), ConflictPage(), Timeline(), RecapBoard(), HeadlineCard(), QuoteCard(), ForensicExamination(),
    EvidenceCheck(), FactCard())}


def graphic(kind: str) -> Graphic:
    try:
        return GRAPHICS[kind]
    except KeyError:
        raise GraphicUnavailable(f"unknown graphic type {kind!r}") from None

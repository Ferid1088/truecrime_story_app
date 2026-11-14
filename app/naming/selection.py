"""The title options of a film and how a title is chosen: no model call. The options are the best-ranked eligible titles that really differ from each other (two options share at most
`option_max_overlap` of their words and have different angles), the chosen one is marked, and the story hook is the family's editorial concept the writer already stated. The label of an
option is the writer's own `angle` (what the title is built on), never a fixed category."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, CaseTitleCandidate
from app.identity.titles import get_identity

OPTIONS = 3


def rank_score(row: CaseTitleCandidate) -> float:
    return json.loads(row.critic_json or "{}").get("rank_score") or 0.0


def angle_of(row: CaseTitleCandidate) -> str:
    return " ".join(str(json.loads(row.critic_json or "{}").get("angle") or "").split())


def eligible(db: Session, case_id: int, language: str) -> list[CaseTitleCandidate]:
    rows = db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case_id, CaseTitleCandidate.language == language, CaseTitleCandidate.status != "rejected").all()
    return sorted(rows, key=rank_score, reverse=True)


def within_margin(best: CaseTitleCandidate, row: CaseTitleCandidate) -> bool:
    return rank_score(best) - rank_score(row) <= ai_config.case_naming.pair_margin


def words(text: str) -> set[str]:
    return {w.casefold() for w in re.findall(r"\w+", text)}


def overlap(a: str, b: str) -> float:
    """The share of the shorter title's words that the other one has too (0 to 1)."""
    wa, wb = words(a), words(b)
    return len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0


def closeness(row: CaseTitleCandidate, chosen: list[CaseTitleCandidate]) -> float:
    return max((overlap(row.title, c.title) for c in chosen), default=0.0)


def different(row: CaseTitleCandidate, chosen: list[CaseTitleCandidate]) -> bool:
    return closeness(row, chosen) <= ai_config.case_naming.option_max_overlap and all(not angle_of(row) or angle_of(row).casefold() != angle_of(c).casefold() for c in chosen)


def options(db: Session, case: Case, language: str) -> dict:
    """{options:[{id A/B/C, angle, title, candidate_id, rank_score, selected}], hook}: the best title, then each time the title with the best rank minus its likeness to those chosen (and not too like
    one of them), then the best of the rest."""
    rows = eligible(db, case.id, language)
    chosen: list[CaseTitleCandidate] = []
    diversity = ai_config.case_naming.option_diversity
    while len(chosen) < OPTIONS:
        fits = [r for r in rows if r not in chosen and different(r, chosen)]
        if not fits:
            break
        chosen.append(max(fits, key=lambda r: rank_score(r) - diversity * closeness(r, chosen)))
    chosen += [r for r in rows if r not in chosen][:OPTIONS - len(chosen)]
    ident = get_identity(db, case.id, language)
    return {"options": [{"id": "ABC"[i], "angle": angle_of(r), "title": r.title, "candidate_id": r.id, "rank_score": round(rank_score(r), 3),
                         "selected": bool(ident and ident.editorial_title == r.title)} for i, r in enumerate(chosen)],
            "hook": next((r.editorial_concept for r in rows if r.editorial_concept), None)}


def repeat_share(cover_title: str, youtube_title: str) -> float:
    """The share of the cover title's words that the YouTube title already says (0 to 1)."""
    mine = words(cover_title)
    return len(mine & words(youtube_title)) / len(mine) if mine else 0.0

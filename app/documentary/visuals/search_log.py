"""The search ledger of a case: which provider already ran which query. A query is never run
twice on the same provider (a provider added later still runs the old queries once)."""

from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session

from app.db.models import VisualSearchLog
from app.documentary.visuals.planner import fold

KEY_CHARS = 300


def query_key(query: str) -> str:
    return " ".join(fold(query).split())[:KEY_CHARS]


class SearchLedger:
    def __init__(self, db: Session, case_id: int):
        self.db, self.case_id = db, case_id
        self._done = {(r.provider, r.query_key) for r in db.query(
            VisualSearchLog.provider, VisualSearchLog.query_key).filter(
            VisualSearchLog.case_id == case_id)}

    def seen(self, provider: str, query: str) -> bool:
        return (provider, query_key(query)) in self._done

    def record(self, provider: str, query: str, *, kind: str, found_during: str | None,
               entity_key: str | None, candidates: int) -> None:
        key = query_key(query)
        self._done.add((provider, key))
        self.db.add(VisualSearchLog(case_id=self.case_id, provider=provider, query_key=key,
                                    query=query[:2000], kind=kind, found_during=found_during,
                                    entity_key=entity_key, candidates=candidates))
        self.db.commit()

    def counts(self) -> dict[str, int]:
        return dict(Counter(provider for provider, _ in self._done))

# The providers a text query is sent to (the other providers read the case's own pages).
QUERY_PROVIDERS = ("wikimedia", "brave_images")

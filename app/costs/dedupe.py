"""A paid call is written twice: by the network layer (host of the provider, with its tokens and a price) and by the budget ledger (stage and model, the same tokens
and price). Counting both pays it twice on screen. The row with the stage (the ledger's) is the one that counts; its twin from the network layer is marked."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy.orm import Session

from app.costs.registry import registry
from app.db.models import ProviderCall


def mark_duplicates(db: Session, *, case_id: int | None = None, since=None) -> int:
    """Marks every unmarked network row that has a ledger twin (same case, same tokens in and out, within the configured seconds, one twin for one row). Returns how many."""
    cfg = registry()
    window = timedelta(seconds=cfg["duplicate_window_seconds"])
    base = [ProviderCall.is_test_data.is_(False), ProviderCall.tokens_in.isnot(None), ProviderCall.cost_eur.isnot(None)]
    if case_id is not None:
        base.append(ProviderCall.case_id == case_id)
    if since is not None:
        base.append(ProviderCall.created_at >= since - window)
    wires = (db.query(ProviderCall).filter(*base, ProviderCall.host.in_(cfg["wire_hosts"]), ProviderCall.duplicate_of.is_(None)).order_by(ProviderCall.created_at).all())
    if not wires:
        return 0
    low, high = wires[0].created_at - window, wires[-1].created_at + window
    ledgers = (db.query(ProviderCall).filter(*base, ProviderCall.host == "budget-ledger", ProviderCall.created_at.between(low, high)).order_by(ProviderCall.created_at).all())
    pool: dict[tuple, list[ProviderCall]] = defaultdict(list)
    for row in ledgers:
        pool[(row.case_id, row.tokens_in, row.tokens_out)].append(row)
    claimed = {d for (d,) in db.query(ProviderCall.duplicate_of).filter(ProviderCall.duplicate_of.isnot(None)).all()}
    marked = 0
    for wire in wires:
        best = None
        for twin in pool.get((wire.case_id, wire.tokens_in, wire.tokens_out), []):
            gap = abs(twin.created_at - wire.created_at)
            if twin.id not in claimed and gap <= window and (best is None or gap < best[0]):
                best = (gap, twin)
        if best is not None:
            wire.duplicate_of = best[1].id
            claimed.add(best[1].id)
            marked += 1
    db.commit()
    return marked

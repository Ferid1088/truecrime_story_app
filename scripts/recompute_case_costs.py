"""Recompute mispriced provider_calls rows for a case, live, in place.

ROADMAP.md Part 0b live proof: case 5's real ledger had 223 "cheap"-role
calls stored under a dated gateway snapshot of the model it actually
requested (e.g. an -YYYYMMDD suffix) and booked at cost_eur=0.0 because the
exact-string price lookup never matched it — app.services.cost_model.
model_cost_eur now normalizes that suffix away. This script finds every row
still carrying the old, wrong EUR 0 and recomputes it with the real stored
tokens, in place.

    python -m scripts.recompute_case_costs <case_id> [--apply]

Without --apply it only reports what would change (dry run, the default).
Only rows that are (a) ok, (b) estimated=True, (c) cost_eur in (0.0, None),
and (d) now priceable from their own stored tokens_in/tokens_out are
touched — nothing is invented for a row with no usable price."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import or_

from app.db.base import SessionLocal
from app.db.models import ProviderCall
from app.services.budget import BudgetService
from app.services.cost_model import model_cost_eur


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id", type=int)
    parser.add_argument("--apply", action="store_true",
                        help="write the recomputed cost_eur values (default: dry run)")
    args = parser.parse_args()

    db = SessionLocal()
    before = BudgetService(db).snapshot(args.case_id)

    candidates = (
        db.query(ProviderCall)
        .filter(
            ProviderCall.case_id == args.case_id,
            ProviderCall.ok.is_(True),
            ProviderCall.estimated.is_(True),
            or_(ProviderCall.cost_eur == 0.0, ProviderCall.cost_eur.is_(None)),
            ProviderCall.tokens_in.isnot(None),
            ProviderCall.tokens_out.isnot(None),
        )
        .all()
    )

    changed = []
    for row in candidates:
        new_cost = model_cost_eur(row.model, row.tokens_in, row.tokens_out)
        if new_cost is None:
            continue
        changed.append((row, new_cost))

    print(f"case {args.case_id}: {len(candidates)} zero/estimated rows with real "
          f"tokens, {len(changed)} now priceable")
    by_model: dict[str, list[float]] = {}
    for row, new_cost in changed:
        by_model.setdefault(row.model, []).append(new_cost)
    for model, costs in sorted(by_model.items()):
        print(f"  model={model!r}: {len(costs)} rows, recomputed sum=EUR {sum(costs):.6f}")

    recomputed_total = sum(cost for _, cost in changed)
    print(f"spent_eur before: EUR {before.spent_eur:.6f}")

    if args.apply:
        for row, new_cost in changed:
            row.cost_eur = new_cost
            row.estimated = False
        db.commit()
        after = BudgetService(db).snapshot(args.case_id)
        print(f"spent_eur after:  EUR {after.spent_eur:.6f}  (written)")
    else:
        print(f"spent_eur after:  EUR {before.spent_eur + recomputed_total:.6f}  "
              "(dry run — not written)")
        print("Pass --apply to write the recomputed cost_eur values.")


if __name__ == "__main__":
    sys.exit(main())

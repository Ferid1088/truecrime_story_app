"""Persist explicit human approval for a complete EpistemicContractSet."""

from __future__ import annotations

import argparse

from app.db.base import SessionLocal
from app.db.models import EpistemicContractSet
from app.longform.service import LongformValidationError, approve_contract_set


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract_set_id", type=int)
    parser.add_argument(
        "--confirm", action="store_true",
        help="persist approval; without this flag the command only previews the change",
    )
    args = parser.parse_args()
    with SessionLocal() as db:
        row = db.get(EpistemicContractSet, args.contract_set_id)
        if not row:
            raise SystemExit(f"contract set {args.contract_set_id} not found")
        print(
            f"contract_set_id={row.id} case_id={row.case_id} status={row.status} "
            f"claims={len(row.claims)} proposed={sum(c.review_status == 'proposed' for c in row.claims)}"
        )
        if not args.confirm:
            print("DRY_RUN: rerun with --confirm to mark all claims approved and set status=approved")
            return
        try:
            approved = approve_contract_set(db, row.id)
        except LongformValidationError as exc:
            raise SystemExit(str(exc)) from exc
        print(
            f"APPROVED: contract_set_id={approved.id} status={approved.status} "
            f"claims={len(approved.claims)} approved="
            f"{sum(c.review_status == 'approved' for c in approved.claims)}"
        )


if __name__ == "__main__":
    main()

"""Book the estimated HeyGen cost of every finished host video that has no ledger entry yet (one
entry per video, keyed by the provider's job id; running it again adds nothing)."""
from app.db.base import SessionLocal
from app.documentary import heygen_cost


def main() -> None:
    with SessionLocal() as db:
        before = db.execute(__import__("sqlalchemy").text(
            "select count(*) from provider_calls where stage = :s"), {"s": heygen_cost.STAGE}).scalar()
        rows = heygen_cost.book_existing(db)
        after = db.execute(__import__("sqlalchemy").text(
            "select count(*) from provider_calls where stage = :s"), {"s": heygen_cost.STAGE}).scalar()
        for row in rows:
            print(row.case_id, row.request_id, f"{row.units:.2f} s", f"EUR {row.cost_eur:.4f}")
        print(f"entries before {before}, after {after}")


if __name__ == "__main__":
    main()

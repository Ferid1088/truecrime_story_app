"""Database schema setup, called once at startup (app/main.py).

The schema itself is owned by Alembic (migrations/, app/db/migrate.py). This module
only runs the migrations and then backfills ids that older rows may lack.
"""

from app.db.base import engine


def _backfill_case_uids():
    from app.db.base import SessionLocal
    from app.identity.titles import backfill_case_uids

    with SessionLocal() as _db:
        backfill_case_uids(_db)


def init_schema() -> None:
    from app.db.migrate import upgrade_database

    upgrade_database(engine)
    _backfill_case_uids()

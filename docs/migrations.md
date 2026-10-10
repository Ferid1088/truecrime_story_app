# Database migrations

The schema is owned by Alembic (`migrations/`). The app runs the migrations itself at startup
(`app/db/migrate.py`); nothing edits tables by hand any more.

## Change the schema

1. Edit the model in `app/db/models.py`.
2. `python -m alembic revision --autogenerate -m "what changed"` and read the generated file in
   `migrations/versions/`. Fix anything it got wrong (renames, data fixes).
3. `python -m alembic upgrade head` on a COPY of the database, then run the gate
   (`scripts/gate.sh`). `tests/test_migrations.py` fails if models and migrations disagree.
4. Before touching the real database: `python scripts/db_tools.py backup truecrime.db backups/`.

## Revisions

* `0001` legacy baseline: exact structure of the production database on 2026-10-10
  (`migrations/legacy_schema.sql`). An older database without `alembic_version` is checked against it
  and stamped; if it does not match, startup stops and changes nothing.
* `0002` aligns that structure with the models (NOT NULL columns, indexes, one foreign key).

## Undo

Migrations are forward-only. To undo a step, restore the backup taken before it
(`scripts/db_tools.py verify <backup> <baseline.json>` proves the backup is intact).

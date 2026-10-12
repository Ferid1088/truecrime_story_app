#!/usr/bin/env python3
"""Fail if a fresh Alembic database differs from the ORM metadata."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.command import upgrade
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.db.models  # noqa: F401
from app.db import migrate
from app.db.base import Base


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="truecrime-model-schema-") as directory:
        path = Path(directory) / "schema.db"
        engine = create_engine(f"sqlite:///{path}")
        try:
            upgrade(migrate.alembic_config(f"sqlite:///{path}"), "head")
            with engine.connect() as connection:
                context = MigrationContext.configure(
                    connection, opts={"compare_type": True}
                )
                differences = compare_metadata(context, Base.metadata)
        finally:
            engine.dispose()
    if differences:
        print("Alembic migrations do not equal ORM metadata:")
        for difference in differences:
            print(f"  {difference}")
        return 1
    print("Alembic migrations equal ORM metadata on a fresh SQLite database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

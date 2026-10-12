#!/usr/bin/env python3
"""Clean the backed-up application database while preserving operator state."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine, inspect, text

PRESERVED = {"alembic_version", "app_settings"}


def table_counts(connection, tables: list[str]) -> dict[str, int]:
    return {
        table: int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())
        for table in tables
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != "CLEAN_TRUECRIME_LIVE":
        parser.error("--confirm must be CLEAN_TRUECRIME_LIVE")

    engine = create_engine(args.database_url)
    with engine.begin() as connection:
        tables = sorted(inspect(connection).get_table_names(schema="public"))
        missing = PRESERVED.difference(tables)
        if missing:
            raise RuntimeError(f"expected preserved tables are missing: {sorted(missing)}")
        before = table_counts(connection, tables)
        targets = [table for table in tables if table not in PRESERVED]
        quoted = ", ".join(f'"{table}"' for table in targets)
        connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        after = table_counts(connection, tables)
        print(json.dumps({"preserved": sorted(PRESERVED), "before": before, "after": after}, default=str, sort_keys=True))


if __name__ == "__main__":
    main()

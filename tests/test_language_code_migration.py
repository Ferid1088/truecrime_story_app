import importlib.util
import pathlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_migration_0030_rewrites_regional_tags_to_the_base_language():
    path = pathlib.Path("migrations/versions/0030_primary_language_codes.py")
    spec = importlib.util.spec_from_file_location("m0030", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for table in module._TABLES:
            conn.execute(sa.text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, language TEXT)"))
            conn.execute(sa.text(
                f"INSERT INTO {table} (language) VALUES ('en-US'), ('de_DE'), ('fa'), ('unknown')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        for table in module._TABLES:
            languages = [r[0] for r in conn.execute(sa.text(
                f"SELECT language FROM {table} ORDER BY id"))]
            assert languages == ["en", "de", "fa", "unknown"]

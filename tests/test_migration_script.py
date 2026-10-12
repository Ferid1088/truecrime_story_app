from pathlib import Path


def test_migration_script_is_local_postgres_only_and_reversible():
    text = (Path(__file__).parents[1] / "scripts/check_migrations.sh").read_text()
    assert "createdb" in text and "dropdb" in text
    assert "alembic upgrade head" in text
    assert "alembic downgrade -1" in text
    assert "docker" not in text.lower()

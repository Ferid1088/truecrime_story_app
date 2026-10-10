"""Alembic environment: the database comes from app settings, the target is the ORM metadata."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.db.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    url = _url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=_is_sqlite(url), compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def _migrate(connection, url: str) -> None:
    if _is_sqlite(url):
        # Batch (table-rebuild) migrations must not trip over foreign keys mid-copy.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=_is_sqlite(url), compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
    if _is_sqlite(url):
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def run_migrations_online() -> None:
    url = _url()
    shared = config.attributes.get("connection")
    if shared is not None:  # the caller owns the connection and its commit
        _migrate(shared, url)
        return
    connect_args = {"check_same_thread": False, "timeout": 15} if _is_sqlite(url) else {}
    engine = create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)
    try:
        with engine.begin() as connection:  # commits on success, rolls back on error
            _migrate(connection, url)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

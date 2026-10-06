from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC timestamp that round-trips correctly on SQLite.

    SQLite has no tz-aware storage; values are persisted as UTC and reads
    always return aware datetimes (including rows written before the
    tz-aware refactor, which are interpreted as UTC). On dialects that
    support it (e.g. Postgres) the column is a native tz-aware type.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(DateTime())
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        aware = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return aware.replace(tzinfo=None)
        return aware

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

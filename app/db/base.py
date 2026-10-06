from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

_SQLITE = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 15} if _SQLITE else {}

engine = create_engine(settings.database_url, connect_args=connect_args)

if _SQLITE:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        # Parallel documentaries and the UI read while jobs write: WAL lets
        # readers continue during a write; busy_timeout makes a writer wait
        # for the lock instead of failing at once.
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=15000")
        except Exception:  # read-only or in-memory databases
            pass
        finally:
            cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

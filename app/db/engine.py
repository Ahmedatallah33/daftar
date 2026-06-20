import os
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, scoped_session

from app.config import DB_URL, ensure_dirs
from app.db.models import Base

ensure_dirs()

# Allow tests (or advanced users) to redirect all DB access to a different
# database without touching the real data/teacher.db. Falls back to the
# configured default URL.
_ACTIVE_DB_URL = os.environ.get("TEACHER_DB_URL", DB_URL)

engine = create_engine(_ACTIVE_DB_URL, echo=False, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))


def configure_engine(db_url: str):
    """Rebind the engine + session factory to a different database URL.

    Used by the test-suite to point all database access at a throwaway,
    temporary SQLite file. Returns the new engine.
    """
    global engine
    SessionLocal.remove()
    engine.dispose()
    engine = create_engine(db_url, echo=False, future=True)
    SessionLocal.configure(bind=engine)
    return engine

_MIGRATIONS = {
    "sessions": [
        ("is_free", "BOOLEAN DEFAULT 0"),
        ("lesson_summary", "TEXT DEFAULT ''"),
    ],
    "invoices": [
        ("is_paid", "BOOLEAN DEFAULT 0"),
        ("paid_at", "DATETIME"),
    ],
    "students": [
        ("day_schedules", "TEXT DEFAULT '{}'"),
        ("parent_phone", "TEXT DEFAULT ''"),
        ("zoom_link_name", "TEXT DEFAULT ''"),
        ("custom_fields", "TEXT DEFAULT '[]'"),
        ("whatsapp_group_link", "TEXT DEFAULT ''"),
    ],
}


def _apply_column_migrations():
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _MIGRATIONS.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_type in columns:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))


def init_db():
    Base.metadata.create_all(engine)
    _apply_column_migrations()


def get_session():
    return SessionLocal()


@contextmanager
def session_scope():
    """Transactional scope: commits on success, rolls back on error.

    Preferred over bare ``get_session()`` for write operations so a failed
    operation never leaves the shared (scoped) session in a broken state::

        with session_scope() as s:
            s.add(obj)
    """
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise

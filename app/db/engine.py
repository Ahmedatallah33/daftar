import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import scoped_session, sessionmaker

from app.config import BACKUPS_DIR, DB_PATH, DB_URL, ensure_dirs
from app.db.models import Base
from app.db.safety import integrity_check, online_backup


BUSY_TIMEOUT_MS = 10_000
SCHEMA_VERSION = 2


class DatabaseSafetyError(RuntimeError):
    pass


class DatabaseBusyError(DatabaseSafetyError):
    pass


class InvalidDatabaseError(DatabaseSafetyError):
    pass


_MIGRATIONS = {
    "sessions": [
        ("is_free", "BOOLEAN DEFAULT 0"),
        ("lesson_summary", "TEXT DEFAULT ''"),
    ],
    "invoices": [
        ("cycle_signature", "TEXT DEFAULT ''"),
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


def _create_engine(db_url: str):
    created = create_engine(
        db_url,
        echo=False,
        future=True,
        connect_args={"timeout": BUSY_TIMEOUT_MS / 1000},
    )

    @event.listens_for(created, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

    return created


_ACTIVE_DB_URL = os.environ.get("TEACHER_DB_URL", DB_URL)
engine = _create_engine(_ACTIVE_DB_URL)
SessionLocal = scoped_session(
    sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
)


def configure_engine(db_url: str):
    """Rebind all database access to another SQLite database."""
    global engine
    SessionLocal.remove()
    engine.dispose()
    engine = _create_engine(db_url)
    SessionLocal.configure(bind=engine)
    return engine


def current_database_path() -> Path:
    database = engine.url.database
    if not database:
        raise InvalidDatabaseError("لم يتم تحديد مسار قاعدة البيانات.")
    return Path(database).expanduser().resolve()


def _expected_columns() -> dict[str, set[str]]:
    return {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }


def _allowed_legacy_columns() -> dict[str, set[str]]:
    return {
        table: {column_name for column_name, _definition in columns}
        for table, columns in _MIGRATIONS.items()
    }


def _schema_version(connection) -> int | None:
    tables = set(inspect(connection).get_table_names())
    if "schema_meta" not in tables:
        return None
    row = connection.execute(
        text("SELECT value FROM schema_meta WHERE key='schema_version'")
    ).first()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError) as error:
        raise InvalidDatabaseError("قيمة إصدار قاعدة البيانات غير صالحة.") from error


def _validate_existing_schema(connection) -> tuple[bool, list[str], bool]:
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    expected = _expected_columns()
    core_tables = set(expected)
    application_tables = existing_tables & core_tables

    if not existing_tables:
        changes = [f"create table {name}" for name in sorted(core_tables)]
        changes.append(f"schema version None -> {SCHEMA_VERSION}")
        return True, changes, True

    if not application_tables:
        raise InvalidDatabaseError("قاعدة البيانات لا تحتوي على جداول Teacher Hub المعروفة.")

    missing_tables = core_tables - application_tables
    if missing_tables:
        names = ", ".join(sorted(missing_tables))
        raise InvalidDatabaseError(
            f"مخطط قاعدة البيانات جزئي؛ الجداول الأساسية المفقودة: {names}"
        )

    has_schema_meta = "schema_meta" in existing_tables
    version = _schema_version(connection)
    if has_schema_meta and version is None:
        raise InvalidDatabaseError("جدول إصدار قاعدة البيانات غير مكتمل.")
    if version not in (None, 1, SCHEMA_VERSION):
        if version is not None and version > SCHEMA_VERSION:
            raise InvalidDatabaseError(
                f"إصدار قاعدة البيانات {version} أحدث من إصدار التطبيق {SCHEMA_VERSION}."
            )
        raise InvalidDatabaseError(f"إصدار قاعدة البيانات غير معروف: {version}.")

    allowed = _allowed_legacy_columns()
    changes = []
    for table_name, expected_columns in expected.items():
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing = expected_columns - actual_columns
        if version == SCHEMA_VERSION and missing:
            fields = ", ".join(sorted(missing))
            raise InvalidDatabaseError(
                f"مخطط الإصدار الحالي غير مكتمل في {table_name}: {fields}"
            )
        unsafe_missing = missing - allowed.get(table_name, set())
        if unsafe_missing:
            fields = ", ".join(sorted(unsafe_missing))
            raise InvalidDatabaseError(
                f"مخطط جدول {table_name} غير مكتمل؛ الحقول المفقودة: {fields}"
            )
        changes.extend(f"add {table_name}.{column}" for column in sorted(missing))

    if version != SCHEMA_VERSION:
        changes.append(f"schema version {version} -> {SCHEMA_VERSION}")
    return bool(changes), changes, False


def _apply_column_migrations(connection) -> None:
    inspector = inspect(connection)
    for table, columns in _MIGRATIONS.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column_name, column_type in columns:
            if column_name not in existing:
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}")
                )


def _write_schema_version(connection) -> None:
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_meta "
        "(key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)"
    ))
    connection.execute(text(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', :version) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    ), {"version": str(SCHEMA_VERSION)})


def _validate_current_schema(connection, require_version: bool = True) -> None:
    inspector = inspect(connection)
    expected = _expected_columns()
    for table_name, expected_columns in expected.items():
        if not inspector.has_table(table_name):
            raise InvalidDatabaseError(f"جدول قاعدة البيانات مفقود: {table_name}")
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        missing = expected_columns - actual
        if missing:
            raise InvalidDatabaseError(
                f"جدول {table_name} غير مكتمل: {', '.join(sorted(missing))}"
            )
    if require_version and _schema_version(connection) != SCHEMA_VERSION:
        raise InvalidDatabaseError("لم يكتمل تحديث إصدار قاعدة البيانات.")


def init_db() -> Path | None:
    """Initialize and validate the database, returning a pre-migration backup if made."""
    ensure_dirs()
    database_path = current_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    pre_migration_backup = None

    if database_path.exists() and database_path.stat().st_size > 0:
        valid, detail = integrity_check(database_path)
        if not valid:
            raise InvalidDatabaseError(f"فشل فحص سلامة قاعدة البيانات: {detail}")

    try:
        with engine.connect() as connection:
            needs_migration, _changes, is_fresh = _validate_existing_schema(connection)
    except OperationalError as error:
        raise InvalidDatabaseError(f"تعذر قراءة مخطط قاعدة البيانات: {error}") from error

    if (
        needs_migration
        and not is_fresh
        and database_path.exists()
        and database_path.stat().st_size > 0
    ):
        backup_dir = (
            BACKUPS_DIR
            if database_path == DB_PATH.resolve()
            else database_path.parent / "backups"
        )
        pre_migration_backup = online_backup(
            database_path, backup_dir, prefix="pre_migration"
        )

    try:
        with engine.begin() as connection:
            if is_fresh:
                Base.metadata.create_all(connection)
            else:
                _apply_column_migrations(connection)
            _validate_current_schema(connection, require_version=False)
            _write_schema_version(connection)
            _validate_current_schema(connection, require_version=True)
    except DatabaseSafetyError:
        raise
    except Exception as error:
        raise InvalidDatabaseError(f"فشل تحديث قاعدة البيانات: {error}") from error
    return pre_migration_backup


def get_session():
    return SessionLocal()


def discard_session() -> None:
    try:
        SessionLocal.rollback()
    finally:
        SessionLocal.remove()


@contextmanager
def session_scope():
    """Commit once and make every failed write safe for the next operation."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except OperationalError as error:
        session.rollback()
        SessionLocal.remove()
        if "database is locked" in str(error).lower():
            raise DatabaseBusyError(
                "قاعدة البيانات مشغولة حالياً. أغلق أي عملية أخرى وحاول مرة أخرى."
            ) from error
        raise
    except Exception:
        session.rollback()
        SessionLocal.remove()
        raise

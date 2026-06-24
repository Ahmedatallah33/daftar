import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import engine as engine_mod
from app.db.engine import (
    DatabaseBusyError,
    InvalidDatabaseError,
    get_session,
    session_scope,
)
from app.db.models import Session as SessionModel
from app.db.models import Student
from app.db.safety import integrity_check
from app.services import billing_service as billing
from app.services.group_service import create_group
from app.services.session_service import add_session
from app.services.settings_service import set_setting
from app.services.student_service import create_student


def _create_complete_legacy_schema(database):
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE students (
                id INTEGER PRIMARY KEY, name VARCHAR NOT NULL,
                phone VARCHAR, zoom_link VARCHAR,
                price_per_session FLOAT NOT NULL,
                sessions_per_cycle INTEGER NOT NULL,
                weekly_schedule VARCHAR, session_time VARCHAR,
                notes TEXT, created_at DATETIME, is_active BOOLEAN
            );
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL,
                session_date DATETIME, cycle_number INTEGER,
                counted BOOLEAN, notes TEXT,
                FOREIGN KEY(student_id) REFERENCES students(id)
            );
            CREATE TABLE videos (
                id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL,
                sent_date DATETIME, description VARCHAR, counted BOOLEAN,
                FOREIGN KEY(student_id) REFERENCES students(id)
            );
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL,
                issued_at DATETIME, sessions_count INTEGER,
                videos_count INTEGER, amount FLOAT, pdf_path VARCHAR,
                notes TEXT,
                FOREIGN KEY(student_id) REFERENCES students(id)
            );
            CREATE TABLE settings (
                key VARCHAR PRIMARY KEY NOT NULL, value TEXT
            );
            CREATE TABLE whatsapp_groups (
                id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL,
                invite_link VARCHAR(255) NOT NULL, notes TEXT,
                created_at DATETIME
            );
            INSERT INTO students(name, price_per_session, sessions_per_cycle)
            VALUES ('قديم', 25, 8);
            """
        )


def test_failed_write_does_not_poison_next_write():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(Student(name=None))

    student = create_student("عملية سليمة بعد الفشل")
    assert student.id is not None


def test_foreign_keys_are_enabled_on_every_connection():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(SessionModel(student_id=999_999))

    assert create_student("بعد فشل المفتاح الخارجي").id is not None


def test_database_lock_is_controlled_and_session_recovers(monkeypatch):
    db_path = engine_mod.current_database_path()
    monkeypatch.setattr(engine_mod, "BUSY_TIMEOUT_MS", 100)
    engine_mod.configure_engine(f"sqlite:///{db_path}")
    engine_mod.init_db()

    locker = sqlite3.connect(db_path)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(DatabaseBusyError, match="قاعدة البيانات مشغولة"):
            create_student("محاولة أثناء القفل")
    finally:
        locker.rollback()
        locker.close()

    assert create_student("بعد تحرير القفل").id is not None


def test_two_online_backups_are_distinct_complete_and_valid():
    student = create_student("نسخة كاملة")
    add_session(student.id)
    billing.record_invoice(student.id, 1, 0, 50, "invoice.pdf")
    create_group("مجموعة", "https://chat.whatsapp.com/AbCd1234")
    set_setting("backup_probe", {"ok": True})

    first = billing.backup_database()
    second = billing.backup_database()

    assert first != second
    assert first.exists() and second.exists()
    assert integrity_check(first) == (True, "ok")
    with sqlite3.connect(first) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "students",
            "sessions",
            "videos",
            "invoices",
            "settings",
            "whatsapp_groups",
            "schema_meta",
        } <= tables
        assert connection.execute("SELECT count(*) FROM students").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM invoices").fetchone()[0] == 1


def test_failed_backup_blocks_destructive_reset(monkeypatch):
    student = create_student("لا تحذف")
    add_session(student.id)

    def fail_backup():
        raise OSError("simulated backup failure")

    monkeypatch.setattr(billing, "backup_database", fail_backup)
    with pytest.raises(OSError, match="simulated backup failure"):
        billing.reset_all_activity()

    assert get_session().query(Student).count() == 1
    assert get_session().query(SessionModel).count() == 1


def test_corrupt_database_is_rejected_before_schema_work(tmp_path):
    database = tmp_path / "corrupt.db"
    database.write_bytes(b"this is not sqlite")
    engine_mod.configure_engine(f"sqlite:///{database}")

    with pytest.raises(InvalidDatabaseError, match="سلامة"):
        engine_mod.init_db()

    assert database.read_bytes() == b"this is not sqlite"


def test_partial_schema_fails_without_modification(tmp_path):
    database = tmp_path / "partial.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
    before = database.read_bytes()
    engine_mod.configure_engine(f"sqlite:///{database}")

    with pytest.raises(InvalidDatabaseError, match="مخطط قاعدة البيانات جزئي"):
        engine_mod.init_db()

    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert tables == {"students"}
    assert "schema_meta" not in tables


def test_legacy_schema_gets_pre_migration_backup_and_version(tmp_path):
    database = tmp_path / "legacy.db"
    _create_complete_legacy_schema(database)
    engine_mod.configure_engine(f"sqlite:///{database}")

    backup = engine_mod.init_db()

    assert backup is not None and backup.exists()
    assert integrity_check(backup) == (True, "ok")
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(students)")
        }
        assert "day_schedules" in columns
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == str(engine_mod.SCHEMA_VERSION)


def test_final_validation_failure_keeps_old_schema_version(tmp_path, monkeypatch):
    database = tmp_path / "validation_failure.db"
    engine_mod.configure_engine(f"sqlite:///{database}")
    engine_mod.init_db()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_meta SET value='1' WHERE key='schema_version'"
        )

    real_validation = engine_mod._validate_current_schema

    def fail_validation(connection, require_version=True):
        if require_version:
            raise RuntimeError("simulated final validation failure")
        return real_validation(connection, require_version=False)

    monkeypatch.setattr(engine_mod, "_validate_current_schema", fail_validation)
    with pytest.raises(InvalidDatabaseError, match="simulated final validation failure"):
        engine_mod.init_db()

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
    assert version == "1"

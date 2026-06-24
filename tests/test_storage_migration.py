import sqlite3

from app import config


def _set_user_paths(monkeypatch, root):
    monkeypatch.setattr(config, "USER_ROOT", root)
    monkeypatch.setattr(config, "DATA_DIR", root / "data")
    monkeypatch.setattr(config, "BACKUPS_DIR", root / "backups")
    monkeypatch.setattr(config, "EXPORTS_DIR", root / "exports")
    monkeypatch.setattr(config, "INVOICES_DIR", root / "exports" / "invoices")
    monkeypatch.setattr(config, "LOGS_DIR", root / "logs")
    monkeypatch.setattr(config, "DB_PATH", root / "data" / "teacher.db")


def _valid_database(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES (?)", (value,))


def test_legacy_data_is_copied_without_deleting_sources(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    user = tmp_path / "user"
    legacy_db = legacy / "data" / "teacher.db"
    legacy_backup = legacy / "data" / "backups" / "old.db"
    legacy_invoice = legacy / "exports" / "invoices" / "invoice.pdf"
    _valid_database(legacy_db, "live")
    _valid_database(legacy_backup, "backup")
    legacy_invoice.parent.mkdir(parents=True)
    legacy_invoice.write_bytes(b"%PDF-1.4 test")
    _set_user_paths(monkeypatch, user)

    result = config.migrate_legacy_data([legacy])

    assert result.database_copied is True
    assert result.backups_copied == 1
    assert result.invoices_copied == 1
    assert config.DB_PATH.exists()
    assert (config.BACKUPS_DIR / "old.db").exists()
    assert (config.INVOICES_DIR / "invoice.pdf").exists()
    assert legacy_db.exists() and legacy_backup.exists() and legacy_invoice.exists()
    assert (config.LOGS_DIR / "migration.log").exists()


def test_nonempty_current_database_is_never_overwritten(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    user = tmp_path / "user"
    _valid_database(legacy / "data" / "teacher.db", "legacy")
    _set_user_paths(monkeypatch, user)
    _valid_database(config.DB_PATH, "current")

    result = config.migrate_legacy_data([legacy])

    assert result.database_copied is False
    with sqlite3.connect(config.DB_PATH) as connection:
        value = connection.execute("SELECT value FROM probe").fetchone()[0]
    assert value == "current"


def test_invalid_legacy_database_is_not_selected(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    user = tmp_path / "user"
    database = legacy / "data" / "teacher.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"broken")
    _set_user_paths(monkeypatch, user)

    try:
        config.migrate_legacy_data([legacy])
    except config.LegacyMigrationError:
        pass
    else:
        raise AssertionError("invalid legacy database was accepted")

    assert not config.DB_PATH.exists()
    assert database.read_bytes() == b"broken"

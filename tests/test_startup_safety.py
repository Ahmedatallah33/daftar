import importlib

from app.db import engine as engine_mod


def _run_startup_failure(main_module, monkeypatch, database, qapp):
    from app import startup

    engine_mod.configure_engine(f"sqlite:///{database}")
    backup_dir = database.parent / "backups"
    monkeypatch.setattr(startup, "DB_PATH", database)
    monkeypatch.setattr(startup, "BACKUPS_DIR", backup_dir)
    monkeypatch.setattr(engine_mod, "ensure_dirs", lambda: None)
    monkeypatch.setattr(main_module, "initialize_application_data", engine_mod.init_db)
    monkeypatch.setattr(main_module, "_acquire_single_instance_lock", lambda: True)
    monkeypatch.setattr(main_module, "_set_windows_app_id", lambda: None)
    monkeypatch.setattr(main_module, "load_fonts", lambda _app: None)
    monkeypatch.setattr(main_module, "log_startup_failure", lambda _error: None)
    dialogs = []
    monkeypatch.setattr(
        main_module.QMessageBox,
        "critical",
        lambda parent, title, message: dialogs.append((title, message)),
    )
    result = main_module.main()
    return result, dialogs, backup_dir


def test_importing_theme_does_not_query_database(monkeypatch):
    import app.ui.helpers.theme as theme_module

    def forbidden_query():
        raise AssertionError("theme import queried the database")

    monkeypatch.setattr(theme_module.settings_service, "get_theme", forbidden_query)
    reloaded = importlib.reload(theme_module)
    assert reloaded.theme_manager.theme == "light"


def test_valid_database_loads_persisted_theme_after_startup():
    from app.services.settings_service import set_theme
    from app.ui.helpers.theme import theme_manager

    set_theme("dark")
    theme_manager.load_from_settings()
    assert theme_manager.theme == "dark"


def test_corrupt_database_reaches_arabic_recovery_dialog(
    tmp_path, monkeypatch, qapp
):
    import main

    database = tmp_path / "corrupt.db"
    database.write_bytes(b"not sqlite")
    before = database.read_bytes()
    result, dialogs, backup_dir = _run_startup_failure(
        main, monkeypatch, database, qapp
    )
    assert result == 1
    assert len(dialogs) == 1
    title, message = dialogs[0]
    assert any("\u0600" <= char <= "\u06ff" for char in title)
    assert "لم يحذف" in message
    assert "أغلق التطبيق وكل عملياته" in message
    assert "مجلد النسخ الاحتياطية" in message
    assert str(database) in message
    assert str(backup_dir) in message
    assert database.read_bytes() == before


def test_unreadable_database_reaches_arabic_recovery_dialog(
    tmp_path, monkeypatch, qapp
):
    import main

    database = tmp_path / "teacher.db"
    database.mkdir()
    result, dialogs, backup_dir = _run_startup_failure(
        main, monkeypatch, database, qapp
    )
    assert result == 1
    assert len(dialogs) == 1
    assert "لم يحذف" in dialogs[0][1]
    assert str(database) in dialogs[0][1]
    assert str(backup_dir) in dialogs[0][1]

import importlib

from app import startup


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


def test_startup_recovery_message_remains_arabic_and_preserves_database_guidance(
    tmp_path, monkeypatch
):
    from app import config

    database = tmp_path / "corrupt.db"
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(config, "BACKUPS_DIR", backup_dir)

    message = startup.startup_error_message(RuntimeError("broken"))

    assert any("\u0600" <= char <= "\u06ff" for char in message)
    assert "لم يحذف" in message
    assert "أغلق التطبيق وكل عملياته" in message
    assert "مجلد النسخ الاحتياطية" in message
    assert str(database) in message
    assert str(backup_dir) in message

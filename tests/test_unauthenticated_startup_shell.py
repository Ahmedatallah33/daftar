from __future__ import annotations

import importlib

from app import config


def test_main_starts_account_shell_without_database_initialization(
    tmp_path, monkeypatch, qapp
):
    import main
    from app.ui import account_shell

    original_root = config.USER_ROOT
    config.apply_user_root(tmp_path / "TeacherHub")
    calls = []

    class FakeShell:
        def __init__(self):
            calls.append("shell")

        def showNormal(self):
            calls.append("show")

        def raise_(self):
            calls.append("raise")

        def activateWindow(self):
            calls.append("activate")

    monkeypatch.setattr(main, "_acquire_single_instance_lock", lambda: True)
    monkeypatch.setattr(main, "_set_windows_app_id", lambda: None)
    monkeypatch.setattr(main, "load_fonts", lambda _app: None)
    monkeypatch.setattr(account_shell, "AccountShell", FakeShell)
    monkeypatch.setattr(main.QApplication, "exec", lambda _self: 0)

    try:
        result = main.main()
    finally:
        config.apply_user_root(original_root)

    assert result == 0
    assert calls == ["shell", "show", "raise", "activate"]
    assert not (tmp_path / "TeacherHub" / "data" / "teacher.db").exists()
    assert not (tmp_path / "TeacherHub" / "restore" / "pending_restore.json").exists()


def test_account_shell_does_not_construct_operational_pages(qtbot, monkeypatch):
    from app.ui.account_shell import AccountShell
    from app.ui.pages import students_page

    def forbidden_page(*_args, **_kwargs):
        raise AssertionError("operational page constructed while signed out")

    monkeypatch.setattr(students_page.StudentsPage, "__init__", forbidden_page)

    shell = AccountShell()
    qtbot.addWidget(shell)

    assert not hasattr(shell, "stack")
    assert shell.auth.current_state.name == "SIGNED_OUT"


def test_existing_shared_database_is_not_reached_from_shell(tmp_path, qtbot, monkeypatch):
    from app.ui.account_shell import AccountShell
    from PySide6.QtWidgets import QLabel

    original_root = config.USER_ROOT
    config.apply_user_root(tmp_path / "TeacherHub")
    legacy_db = config.DB_PATH
    legacy_db.parent.mkdir(parents=True)
    legacy_db.write_bytes(b"legacy sentinel")

    try:
        shell = AccountShell()
        qtbot.addWidget(shell)
    finally:
        config.apply_user_root(original_root)

    assert legacy_db.read_bytes() == b"legacy sentinel"
    shell_text = "\n".join(label.text() for label in shell.findChildren(QLabel))
    assert "Teacher Hub" in shell_text


def test_importing_theme_does_not_query_database(monkeypatch):
    import app.ui.helpers.theme as theme_module

    def forbidden_query():
        raise AssertionError("theme import queried the database")

    monkeypatch.setattr(theme_module.settings_service, "get_theme", forbidden_query)
    reloaded = importlib.reload(theme_module)
    assert reloaded.theme_manager.theme == "light"

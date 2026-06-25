from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import restart as app_restart
from app.activation import ActivationCoordinator
from app.db import engine as engine_mod


class _FakeButton:
    def __init__(self, events):
        self.events = events

    def setEnabled(self, enabled):
        self.events.append(f"button:{enabled}")


class _FakeNotifier:
    def __init__(self, events):
        self.events = events

    def stop(self):
        self.events.append("notifier_stop")


class _FakeTimer:
    def __init__(self, events):
        self.events = events

    def stop(self):
        self.events.append("timer_stop")


class _FakeAuth:
    def __init__(self, events, *, fail=False):
        self.events = events
        self.fail = fail

    def sign_out(self):
        self.events.append("auth_sign_out")
        if self.fail:
            raise RuntimeError("sign out failed")


class _FakeWindow:
    def __init__(self, events, *, auth=None):
        self.events = events
        self.account_btn = _FakeButton(events)
        self.notifier = _FakeNotifier(events)
        self.clock_timer = _FakeTimer(events)
        self.account_auth = auth or _FakeAuth(events)
        self._restart_callback = lambda: events.append("launch")

    def close(self):
        self.events.append("close")


class _FakeApp:
    def __init__(self, events):
        self.events = events

    def quit(self):
        self.events.append("quit")

    def processEvents(self):
        return None


class _FakeFunction:
    def __init__(self, function):
        self._function = function
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._function(*args)


class _FakeKernel32:
    def __init__(self, errors):
        self.errors = list(errors)
        self.handles = []
        self.closed = []
        self.last_error = 0
        self.CreateMutexW = _FakeFunction(self._create_mutex)
        self.GetLastError = _FakeFunction(lambda: self.last_error)
        self.CloseHandle = _FakeFunction(self.closed.append)

    def _create_mutex(self, *_args):
        handle = object()
        self.handles.append(handle)
        self.last_error = self.errors.pop(0)
        return handle


def _reset_restart_request():
    app_restart.reset_restart_request()


@pytest.fixture(autouse=True)
def restart_state():
    _reset_restart_request()
    yield
    _reset_restart_request()


def test_single_instance_lock_still_rejects_second_instance(monkeypatch):
    import main

    kernel32 = _FakeKernel32([0, 183])
    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(
            kernel32=kernel32,
            user32=SimpleNamespace(
                FindWindowW=lambda *_args: None,
                ShowWindow=lambda *_args: None,
                SetForegroundWindow=lambda *_args: None,
            ),
        )
    )
    fake_wintypes = SimpleNamespace(LPVOID=object, BOOL=bool, LPCWSTR=str, HANDLE=object)

    monkeypatch.setattr(main, "_win32_modules", lambda: (fake_ctypes, fake_wintypes))
    monkeypatch.setattr(main, "_single_instance_handle", None)

    assert main._acquire_single_instance_lock() is True
    assert main._acquire_single_instance_lock() is False


def test_main_handoff_releases_mutex_before_launch(monkeypatch):
    import main

    events = []
    app_restart.request_restart_after_exit(lambda: events.append("launch"))
    monkeypatch.setattr(main, "_release_single_instance_lock", lambda: events.append("release"))

    assert main._launch_requested_restart_after_exit() is True
    assert events == ["release", "launch"]
    assert not app_restart.restart_requested()


def test_main_handoff_does_not_launch_without_restart_request(monkeypatch):
    import main

    events = []
    monkeypatch.setattr(main, "_release_single_instance_lock", lambda: events.append("release"))
    monkeypatch.setattr(app_restart, "launch_replacement_process", lambda: events.append("launch"))

    assert main._launch_requested_restart_after_exit() is False
    assert events == []


def test_sign_out_tears_down_before_restart_schedule_and_close(monkeypatch):
    from app.ui import main_window

    events = []
    fake_window = _FakeWindow(events)
    monkeypatch.setattr(
        "app.activation.deactivate_account_context",
        lambda: events.append("deactivate_context"),
    )

    real_request = app_restart.request_restart_after_exit

    def request(callback):
        events.append("schedule_restart")
        real_request(callback)

    monkeypatch.setattr(main_window, "request_restart_after_exit", request)
    monkeypatch.setattr(
        main_window.QApplication,
        "instance",
        staticmethod(lambda: _FakeApp(events)),
    )

    main_window.MainWindow._sign_out(fake_window)

    assert events == [
        "button:False",
        "notifier_stop",
        "timer_stop",
        "auth_sign_out",
        "deactivate_context",
        "schedule_restart",
        "close",
        "quit",
    ]
    assert app_restart.restart_requested()


def test_restart_is_not_scheduled_when_teardown_does_not_reach_safe_handoff(monkeypatch):
    from app.ui import main_window

    events = []
    fake_window = _FakeWindow(events, auth=_FakeAuth(events, fail=True))
    monkeypatch.setattr(
        "app.activation.deactivate_account_context",
        lambda: events.append("deactivate_context"),
    )
    monkeypatch.setattr(
        main_window,
        "request_restart_after_exit",
        lambda _callback: events.append("schedule_restart"),
    )

    with pytest.raises(RuntimeError, match="sign out failed"):
        main_window.MainWindow._sign_out(fake_window)

    assert "schedule_restart" not in events
    assert "close" not in events
    assert "quit" not in events
    assert not app_restart.restart_requested()


def test_restart_command_supports_source_and_frozen_modes():
    assert app_restart.build_restart_command(
        executable="python.exe",
        argv=["main.py", "--flag"],
        frozen=False,
    ) == ["python.exe", "main.py", "--flag"]

    assert app_restart.build_restart_command(
        executable="TeacherHub.exe",
        argv=["TeacherHub.exe", "--flag"],
        frozen=True,
    ) == ["TeacherHub.exe", "--flag"]


def test_controlled_sign_out_unbinds_engine_and_does_not_touch_legacy_files(tmp_path):
    from app.account_context import active_account_context
    from app.db.engine import InvalidDatabaseError

    legacy_files = [
        tmp_path / "TeacherHub" / "data" / "teacher.db",
        tmp_path / "TeacherHub" / "data" / "backups" / "old.db",
        tmp_path / "TeacherHub" / "exports" / "students.xlsx",
        tmp_path / "TeacherHub" / "exports" / "invoices" / "invoice.pdf",
        tmp_path / "TeacherHub" / "restore" / "pending_restore.json",
        tmp_path / "TeacherHub" / "identity" / "metadata.json",
    ]
    for path in legacy_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"legacy sentinel")

    events = []
    ActivationCoordinator.controlled_sign_out(_FakeAuth(events))

    assert events == ["auth_sign_out"]
    assert active_account_context() is None
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()
    for path in legacy_files:
        assert path.read_bytes() == b"legacy sentinel"

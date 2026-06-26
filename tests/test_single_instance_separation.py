"""Daftar single-instance separation from the legacy Teacher Hub application.

Fake-only tests (no real Win32, no real processes, no remote calls). They prove
that a running legacy Teacher Hub instance cannot block Daftar startup, that
Daftar uses its own mutex and never targets legacy windows, and that the
controlled restart handoff still releases the correct Daftar mutex first.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import config
from app import restart as app_restart


ERROR_ALREADY_EXISTS = 183
LEGACY_MUTEX = "Global\\TeacherHub_SingleInstanceMutex"
LEGACY_WINDOW_TITLE = "Teacher Hub — إدارة الحصص"


class _FakeFunction:
    """A callable that also accepts ctypes-style .argtypes/.restype writes."""

    def __init__(self, function):
        self._function = function
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._function(*args)


class _FakeMutexNamespace:
    """Simulates the Win32 named-mutex namespace keyed by exact name."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created_names: list[str] = []
        self.closed: list[object] = []
        self.last_error = 0

    def create_mutex(self, _attrs, _initial_owner, name):
        self.created_names.append(name)
        if name in self.existing:
            self.last_error = ERROR_ALREADY_EXISTS
        else:
            self.existing.add(name)
            self.last_error = 0
        return object()


class _FakeUser32:
    def __init__(self, windows=None):
        self.windows = dict(windows or {})  # title -> hwnd
        self.find_titles: list[str] = []
        self.shown: list[tuple] = []
        self.foregrounded: list[object] = []
        self.FindWindowW = _FakeFunction(self._find)
        self.ShowWindow = _FakeFunction(lambda hwnd, cmd: self.shown.append((hwnd, cmd)))
        self.SetForegroundWindow = _FakeFunction(self.foregrounded.append)

    def _find(self, _class, title):
        self.find_titles.append(title)
        return self.windows.get(title, 0)


def _install_fake_win32(monkeypatch, mutex_ns, user32):
    import main

    kernel32 = SimpleNamespace(
        CreateMutexW=_FakeFunction(mutex_ns.create_mutex),
        GetLastError=_FakeFunction(lambda: mutex_ns.last_error),
        CloseHandle=_FakeFunction(mutex_ns.closed.append),
    )
    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(kernel32=kernel32, user32=user32)
    )
    fake_wintypes = SimpleNamespace(LPVOID=object, BOOL=bool, LPCWSTR=str, HANDLE=object)
    monkeypatch.setattr(main, "_win32_modules", lambda: (fake_ctypes, fake_wintypes))
    monkeypatch.setattr(main, "_single_instance_handle", None)
    # Keep diagnostics off the real installation log during unit tests.
    monkeypatch.setattr(main, "_record_startup_diagnostic", lambda _event: None)
    return main


@pytest.fixture(autouse=True)
def _restart_state():
    app_restart.reset_restart_request()
    yield
    app_restart.reset_restart_request()


def test_legacy_teacher_hub_mutex_does_not_block_daftar(monkeypatch):
    # The legacy mutex is already held; Daftar must still acquire its own.
    mutex_ns = _FakeMutexNamespace(existing={LEGACY_MUTEX})
    user32 = _FakeUser32()
    main = _install_fake_win32(monkeypatch, mutex_ns, user32)

    assert main._acquire_single_instance_lock() is True
    assert mutex_ns.created_names == [config.DAFTAR_SINGLE_INSTANCE_MUTEX]
    # The legacy name was never created/used by Daftar.
    assert LEGACY_MUTEX not in mutex_ns.created_names


def test_daftar_uses_dedicated_mutex_name(monkeypatch):
    mutex_ns = _FakeMutexNamespace()
    main = _install_fake_win32(monkeypatch, mutex_ns, _FakeUser32())

    main._acquire_single_instance_lock()

    assert config.DAFTAR_SINGLE_INSTANCE_MUTEX == "Global\\Daftar_SingleInstanceMutex"
    assert mutex_ns.created_names == [config.DAFTAR_SINGLE_INSTANCE_MUTEX]


def test_first_daftar_acquires_second_is_rejected(monkeypatch):
    mutex_ns = _FakeMutexNamespace()
    main = _install_fake_win32(monkeypatch, mutex_ns, _FakeUser32())

    assert main._acquire_single_instance_lock() is True
    assert main._acquire_single_instance_lock() is False


def test_second_instance_does_not_target_legacy_window(monkeypatch):
    # Daftar mutex already held -> second instance hits the blocked path.
    mutex_ns = _FakeMutexNamespace(existing={config.DAFTAR_SINGLE_INSTANCE_MUTEX})
    # A legacy window is present; Daftar must never look it up or foreground it.
    user32 = _FakeUser32(windows={LEGACY_WINDOW_TITLE: 4242})
    main = _install_fake_win32(monkeypatch, mutex_ns, user32)

    assert main._acquire_single_instance_lock() is False
    assert LEGACY_WINDOW_TITLE not in user32.find_titles
    assert set(user32.find_titles).issubset(set(main.DAFTAR_FOREGROUND_WINDOW_TITLES))
    assert user32.foregrounded == []  # legacy hwnd never foregrounded


def test_second_instance_foregrounds_existing_daftar_window(monkeypatch):
    mutex_ns = _FakeMutexNamespace(existing={config.DAFTAR_SINGLE_INSTANCE_MUTEX})
    daftar_title = config.DAFTAR_SIGN_IN_WINDOW_TITLE
    user32 = _FakeUser32(windows={daftar_title: 777})
    main = _install_fake_win32(monkeypatch, mutex_ns, user32)

    assert main._acquire_single_instance_lock() is False
    assert user32.foregrounded == [777]
    assert user32.shown and user32.shown[-1][0] == 777


def test_handoff_releases_daftar_mutex_before_launching_replacement(monkeypatch):
    mutex_ns = _FakeMutexNamespace()
    main = _install_fake_win32(monkeypatch, mutex_ns, _FakeUser32())
    assert main._acquire_single_instance_lock() is True
    created_handle = main._single_instance_handle

    order: list[str] = []
    original_release = main._release_single_instance_lock

    def tracked_release():
        order.append("release")
        original_release()

    monkeypatch.setattr(main, "_release_single_instance_lock", tracked_release)
    app_restart.request_restart_after_exit(lambda: order.append("launch"))

    assert main._launch_requested_restart_after_exit() is True
    assert order == ["release", "launch"]
    assert mutex_ns.closed == [created_handle]  # exactly the Daftar handle
    assert not app_restart.restart_requested()


def test_source_mode_restart_command_is_correct():
    assert app_restart.build_restart_command(
        executable="python.exe",
        argv=["main.py", "--flag"],
        frozen=False,
    ) == ["python.exe", "main.py", "--flag"]


def test_pyinstaller_mode_restart_command_is_correct():
    assert app_restart.build_restart_command(
        executable="Daftar.exe",
        argv=["Daftar.exe", "--flag"],
        frozen=True,
    ) == ["Daftar.exe", "--flag"]


def test_startup_diagnostic_is_whitelisted_and_non_sensitive(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(config, "INSTALLATION_LOGS_DIR", tmp_path / "installation_logs")

    main._record_startup_diagnostic("bogus_event_with_secret")
    assert not (tmp_path / "installation_logs" / "startup.log").exists()

    main._record_startup_diagnostic("second_daftar_instance_blocked")
    log_path = tmp_path / "installation_logs" / "startup.log"
    contents = log_path.read_text(encoding="utf-8")
    assert "second_daftar_instance_blocked" in contents
    for sensitive in ("token", "refresh", "@", "Provider/Supabase", "Bearer"):
        assert sensitive not in contents


def test_single_instance_path_does_not_touch_legacy_data(tmp_path, monkeypatch):
    root = tmp_path / "TeacherHub"
    original_root = config.USER_ROOT
    config.apply_user_root(root)
    legacy_files = [
        root / "data" / "teacher.db",
        root / "data" / "backups" / "old.db",
        root / "exports" / "students.xlsx",
        root / "exports" / "invoices" / "invoice.pdf",
        root / "restore" / "pending_restore.json",
        root / "identity" / "metadata.json",
    ]
    for path in legacy_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"legacy sentinel")

    try:
        mutex_ns = _FakeMutexNamespace()
        user32 = _FakeUser32()
        main = _install_fake_win32(monkeypatch, mutex_ns, user32)

        assert main._acquire_single_instance_lock() is True  # first: acquire
        assert main._acquire_single_instance_lock() is False  # second: blocked path
    finally:
        config.apply_user_root(original_root)

    for path in legacy_files:
        assert path.read_bytes() == b"legacy sentinel"
    assert not (root / "data" / "teacher.db-wal").exists()

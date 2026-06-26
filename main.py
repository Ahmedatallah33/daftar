import sys
import traceback
from pathlib import Path

# Early crash logger — if pythonw crashes silently, prefer stable per-user logs
# and retain the temp directory only as a last-resort fallback.
def _install_crash_logger():
    import tempfile
    try:
        from app.config import INSTALLATION_LOGS_DIR

        INSTALLATION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = INSTALLATION_LOGS_DIR / "crash.log"
    except Exception:
        log_path = Path(tempfile.gettempdir()) / "teacher_hub_crash.log"

    def _excepthook(exc_type, exc_value, exc_tb):
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("Daftar uncaught exception:\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        # Default behavior
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


_install_crash_logger()

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from app.config import (
    DAFTAR_APP_USER_MODEL_ID,
    DAFTAR_SIGN_IN_WINDOW_TITLE,
    DAFTAR_SINGLE_INSTANCE_MUTEX,
    FONTS_DIR,
    ICONS_DIR,
)
from app import restart as app_restart


CAIRO_WEIGHTS = (
    "Cairo-Regular.ttf",
    "Cairo-Medium.ttf",
    "Cairo-SemiBold.ttf",
    "Cairo-Bold.ttf",
)

# Second-instance focus is restricted to windows that are unambiguously Daftar.
# The legacy Teacher Hub operational window is never targeted.
DAFTAR_FOREGROUND_WINDOW_TITLES = (DAFTAR_SIGN_IN_WINDOW_TITLE,)

# Fixed, non-sensitive startup diagnostic vocabulary. No tokens, emails, user
# IDs, workspace IDs, credential targets, or paths are ever recorded.
_ALLOWED_STARTUP_EVENTS = frozenset(
    {"single_instance_acquired", "second_daftar_instance_blocked"}
)

_single_instance_handle = None


def _win32_modules():
    import ctypes
    from ctypes import wintypes

    return ctypes, wintypes


def load_fonts(app: QApplication) -> None:
    preferred_family = None
    for font_name in CAIRO_WEIGHTS:
        path = FONTS_DIR / font_name
        if path.exists():
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0 and preferred_family is None:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    preferred_family = families[0]

    if preferred_family is None:
        for fallback in ("Tajawal-Regular.ttf", "Amiri-Regular.ttf"):
            p = FONTS_DIR / fallback
            if p.exists():
                fid = QFontDatabase.addApplicationFont(str(p))
                if fid >= 0:
                    fams = QFontDatabase.applicationFontFamilies(fid)
                    if fams:
                        preferred_family = fams[0]
                        break

    if preferred_family:
        app.setFont(QFont(preferred_family, 11))
    else:
        app.setFont(QFont("Segoe UI", 11))


def _acquire_single_instance_lock():
    """Prevent multiple copies of *Daftar* from running at once.

    Uses a Daftar-specific named mutex via Win32. A running legacy Teacher Hub
    process (which owns a different mutex) does not block Daftar. If another
    *Daftar* instance already holds the mutex, we try to bring its sign-in
    window forward — but only when it is unambiguously a Daftar window — and
    exit this launch. The legacy Teacher Hub window is never targeted.
    """
    try:
        ctypes, wintypes = _win32_modules()
    except ImportError:
        return True  # non-windows, don't block

    global _single_instance_handle
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    # Keep the handle alive for the lifetime of the process.
    _single_instance_handle = kernel32.CreateMutexW(
        None, False, DAFTAR_SINGLE_INSTANCE_MUTEX
    )
    last_err = kernel32.GetLastError()
    if last_err == ERROR_ALREADY_EXISTS:
        # Another Daftar instance is running — intentionally blocked, not a
        # crash. Best-effort foreground of the existing Daftar window, then exit.
        _record_startup_diagnostic("second_daftar_instance_blocked")
        _foreground_existing_daftar_window(ctypes)
        return False
    _record_startup_diagnostic("single_instance_acquired")
    return True


def _foreground_existing_daftar_window(ctypes) -> None:
    """Bring an existing Daftar window forward, if one is safely identifiable.

    Only Daftar-specific window titles are searched, so a legacy Teacher Hub
    window is never restored, foregrounded, or otherwise touched.
    """
    try:
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        for title in DAFTAR_FOREGROUND_WINDOW_TITLES:
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                return
    except Exception:
        pass


def _record_startup_diagnostic(event: str) -> None:
    """Append a fixed, non-sensitive startup event to the installation log.

    Distinguishes an intentionally blocked second Daftar instance from an
    unexpected startup failure (the latter is captured by the crash logger).
    Only whitelisted event names are written; no secrets, identifiers, tokens,
    emails, paths, or credential targets are ever recorded.
    """
    if event not in _ALLOWED_STARTUP_EVENTS:
        return
    try:
        from datetime import datetime

        from app.config import INSTALLATION_LOGS_DIR

        INSTALLATION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with open(INSTALLATION_LOGS_DIR / "startup.log", "a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} startup {event}\n")
    except Exception:
        pass


def _release_single_instance_lock() -> None:
    global _single_instance_handle
    if _single_instance_handle is None:
        return
    try:
        ctypes, _wintypes = _win32_modules()
        ctypes.windll.kernel32.CloseHandle(_single_instance_handle)
    except Exception:
        pass
    finally:
        _single_instance_handle = None


def _launch_requested_restart_after_exit() -> bool:
    if not app_restart.restart_requested():
        return False
    _release_single_instance_lock()
    try:
        app_restart.launch_replacement_process()
    finally:
        app_restart.reset_restart_request()
    return True


def _set_windows_app_id():
    """Tell Windows we're our own app so the taskbar uses OUR icon,
    not the generic pythonw.exe one — and so Daftar is never grouped with the
    legacy Teacher Hub application."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            DAFTAR_APP_USER_MODEL_ID
        )
    except Exception:
        pass


def main():
    if not _acquire_single_instance_lock():
        return 0

    _set_windows_app_id()
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft)

    app_icon_path = ICONS_DIR / "app.ico"
    if app_icon_path.exists():
        app.setWindowIcon(QIcon(str(app_icon_path)))

    load_fonts(app)

    from app.ui.helpers.theme import theme_manager

    theme_manager.apply(app)

    from app.ui.account_shell import AccountShell

    window = AccountShell()
    window.showNormal()
    window.raise_()
    window.activateWindow()
    exit_code = app.exec()
    _launch_requested_restart_after_exit()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

import sys
import traceback
from pathlib import Path

# Early crash logger — if pythonw crashes silently (no console), write a log
# to the user's temp dir so we have something to inspect.
def _install_crash_logger():
    import tempfile
    log_path = Path(tempfile.gettempdir()) / "teacher_hub_crash.log"

    def _excepthook(exc_type, exc_value, exc_tb):
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("Teacher Hub uncaught exception:\n")
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

from app.config import FONTS_DIR, ICONS_DIR, ensure_dirs
from app.db.engine import init_db
from app.ui.helpers.theme import theme_manager
from app.ui.main_window import MainWindow


CAIRO_WEIGHTS = (
    "Cairo-Regular.ttf",
    "Cairo-Medium.ttf",
    "Cairo-SemiBold.ttf",
    "Cairo-Bold.ttf",
)


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
    """Prevent multiple copies of Teacher Hub from running at once.

    Uses a named mutex via Win32. If another instance already holds the mutex,
    we try to bring its window to the foreground and exit this launch.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return True  # non-windows, don't block

    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    # Keep the handle alive for the lifetime of the process
    _acquire_single_instance_lock._handle = kernel32.CreateMutexW(
        None, False, "Global\\TeacherHub_SingleInstanceMutex"
    )
    last_err = kernel32.GetLastError()
    if last_err == ERROR_ALREADY_EXISTS:
        # Another instance is running — focus its window and exit
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "Teacher Hub \u2014 \u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u062d\u0635\u0635")
            if hwnd:
                SW_RESTORE = 9
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return False
    return True


def _set_windows_app_id():
    """Tell Windows we're our own app so the taskbar uses OUR icon,
    not the generic pythonw.exe one."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "TeacherHub.Desktop.Manager.1"
        )
    except Exception:
        pass


def main():
    if not _acquire_single_instance_lock():
        return

    _set_windows_app_id()
    ensure_dirs()
    init_db()

    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft)

    app_icon_path = ICONS_DIR / "app.ico"
    if app_icon_path.exists():
        app.setWindowIcon(QIcon(str(app_icon_path)))

    load_fonts(app)
    theme_manager.apply(app)

    window = MainWindow()
    window.showNormal()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

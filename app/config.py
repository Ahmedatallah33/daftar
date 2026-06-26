import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


APP_NAME = "TeacherHub"
APP_RELEASE_REFERENCE = "Sprint 2A"

# Daftar-specific desktop identity. These are intentionally distinct from the
# legacy Teacher Hub application so the two installations never share a Windows
# single-instance mutex, taskbar identity, or second-instance focus target.
DAFTAR_SINGLE_INSTANCE_MUTEX = "Global\\Daftar_SingleInstanceMutex"
DAFTAR_APP_USER_MODEL_ID = "Daftar.Desktop.Manager.1"
# The signed-out AccountShell window title is unambiguously Daftar (the legacy
# Teacher Hub operational window uses a different, shared-looking title), so it
# is the only safe target for second-instance foreground.
DAFTAR_SIGN_IN_WINDOW_TITLE = "Daftar — تسجيل الدخول"
SOURCE_ROOT = Path(__file__).resolve().parent.parent
IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT)) if IS_FROZEN else SOURCE_ROOT
EXECUTABLE_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else SOURCE_ROOT


def _default_user_root() -> Path:
    override = os.environ.get("TEACHER_HUB_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


INSTALLATION_ROOT = _default_user_root()
USER_ROOT = INSTALLATION_ROOT
DATA_DIR = USER_ROOT / "data"
BACKUPS_DIR = USER_ROOT / "backups"
EXPORTS_DIR = USER_ROOT / "exports"
INVOICES_DIR = EXPORTS_DIR / "invoices"
LOGS_DIR = USER_ROOT / "logs"
RESTORE_DIR = USER_ROOT / "restore"
INSTALLATION_LOGS_DIR = INSTALLATION_ROOT / "installation_logs"
IDENTITY_DIR = INSTALLATION_ROOT / "identity"
IDENTITY_METADATA_PATH = IDENTITY_DIR / "metadata.json"

BASE_DIR = RESOURCE_ROOT  # Backward-compatible alias for read-only application assets.
RESOURCES_DIR = RESOURCE_ROOT / "app" / "resources"
FONTS_DIR = RESOURCES_DIR / "fonts"
ICONS_DIR = RESOURCES_DIR / "icons"
STYLES_DIR = RESOURCE_ROOT / "app" / "ui" / "styles"

DB_PATH = DATA_DIR / "teacher.db"
DB_URL = f"sqlite:///{DB_PATH.as_posix()}"

TEACHER_NAME = "المعلم"
CURRENCY = "ج.م"
DEFAULT_SESSIONS_PER_CYCLE = 8

WEEKDAYS = [
    ("SAT", "السبت"),
    ("SUN", "الأحد"),
    ("MON", "الإثنين"),
    ("TUE", "الثلاثاء"),
    ("WED", "الأربعاء"),
    ("THU", "الخميس"),
    ("FRI", "الجمعة"),
]
WEEKDAY_CODES = [c for c, _ in WEEKDAYS]
WEEKDAY_AR = dict(WEEKDAYS)


class LegacyMigrationError(RuntimeError):
    pass


@dataclass
class MigrationResult:
    database_copied: bool = False
    backups_copied: int = 0
    invoices_copied: int = 0
    messages: list[str] = field(default_factory=list)


def ensure_dirs() -> None:
    for path in (DATA_DIR, BACKUPS_DIR, INVOICES_DIR, LOGS_DIR, RESTORE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def apply_user_root(user_root: Path) -> None:
    """Repoint writable runtime paths to an already selected user/account root."""

    global USER_ROOT, DATA_DIR, BACKUPS_DIR, EXPORTS_DIR, INVOICES_DIR
    global LOGS_DIR, RESTORE_DIR, DB_PATH, DB_URL

    USER_ROOT = Path(user_root).expanduser().resolve()
    DATA_DIR = USER_ROOT / "data"
    BACKUPS_DIR = USER_ROOT / "backups"
    EXPORTS_DIR = USER_ROOT / "exports"
    INVOICES_DIR = EXPORTS_DIR / "invoices"
    LOGS_DIR = USER_ROOT / "logs"
    RESTORE_DIR = USER_ROOT / "restore"
    DB_PATH = DATA_DIR / "teacher.db"
    DB_URL = f"sqlite:///{DB_PATH.as_posix()}"


def reset_user_root() -> None:
    """Return runtime writable paths to the installation-level signed-out root."""

    apply_user_root(INSTALLATION_ROOT)


def legacy_roots() -> list[Path]:
    override = os.environ.get("TEACHER_HUB_LEGACY_ROOT")
    candidates = (
        [Path(item) for item in override.split(os.pathsep) if item]
        if override is not None
        else [SOURCE_ROOT, EXECUTABLE_DIR]
    )
    result = []
    for root in candidates:
        resolved = root.expanduser().resolve()
        if resolved != USER_ROOT.resolve() and resolved not in result:
            result.append(resolved)
    return result


def _integrity_ok(path: Path) -> bool:
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return False


def _available_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stem, suffix = destination.stem, destination.suffix
    counter = 1
    while True:
        candidate = destination.with_name(f"{stem}_legacy_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _log_migration(message: str) -> None:
    ensure_dirs()
    timestamp = datetime.now().isoformat(timespec="seconds")
    with (LOGS_DIR / "migration.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def migrate_legacy_data(roots: list[Path] | None = None) -> MigrationResult:
    """Copy legacy project-relative data into the stable per-user location."""
    ensure_dirs()
    result = MigrationResult()
    marker = LOGS_DIR / ".legacy_migration_complete"
    if roots is None and marker.exists():
        _log_migration("legacy migration previously completed; no copy needed")
        return result
    candidates = roots if roots is not None else legacy_roots()
    current_db_has_data = DB_PATH.exists() and DB_PATH.stat().st_size > 0

    for root in candidates:
        root = Path(root).expanduser().resolve()
        legacy_db = root / "data" / "teacher.db"
        if (
            not result.database_copied
            and not current_db_has_data
            and legacy_db.exists()
            and legacy_db.resolve() != DB_PATH.resolve()
        ):
            if not _integrity_ok(legacy_db):
                message = f"Legacy database failed integrity_check: {legacy_db}"
                _log_migration(message)
                raise LegacyMigrationError(message)
            temporary = DATA_DIR / "teacher.db.migrating"
            if temporary.exists():
                temporary.unlink()
            shutil.copy2(legacy_db, temporary)
            if not _integrity_ok(temporary):
                temporary.unlink(missing_ok=True)
                message = f"Copied legacy database failed integrity_check: {legacy_db}"
                _log_migration(message)
                raise LegacyMigrationError(message)
            os.replace(temporary, DB_PATH)
            current_db_has_data = True
            result.database_copied = True
            result.messages.append(f"database copied from {legacy_db}")

        legacy_backups = root / "data" / "backups"
        if legacy_backups.is_dir():
            for source in legacy_backups.glob("*.db"):
                if not _integrity_ok(source):
                    result.messages.append(f"invalid backup skipped: {source}")
                    continue
                destination = _available_destination(BACKUPS_DIR / source.name)
                shutil.copy2(source, destination)
                if not _integrity_ok(destination):
                    destination.unlink(missing_ok=True)
                    result.messages.append(f"invalid copied backup removed: {source}")
                    continue
                result.backups_copied += 1

        legacy_invoices = root / "exports" / "invoices"
        if legacy_invoices.is_dir():
            for source in legacy_invoices.glob("*.pdf"):
                destination = _available_destination(INVOICES_DIR / source.name)
                shutil.copy2(source, destination)
                result.invoices_copied += 1

    if current_db_has_data and not result.database_copied:
        result.messages.append("current user database preserved; legacy database not overwritten")
    summary = (
        f"legacy migration complete: database={result.database_copied}, "
        f"backups={result.backups_copied}, invoices={result.invoices_copied}"
    )
    _log_migration(summary)
    for message in result.messages:
        _log_migration(message)
    marker.write_text(summary, encoding="utf-8")
    return result

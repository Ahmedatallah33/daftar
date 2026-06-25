from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from app import config
from app.db.engine import init_db
from app.services.backup_restore_service import apply_pending_restore

ensure_dirs = config.ensure_dirs
migrate_legacy_data = config.migrate_legacy_data


def _ensure_engine_bound_to_config() -> None:
    from app.db import engine as engine_mod

    try:
        if engine_mod.current_database_path() == config.DB_PATH.resolve():
            return
    except Exception:
        pass
    engine_mod.configure_engine(config.DB_URL)


def initialize_application_data() -> Path | None:
    """Prepare stable user storage, migrate legacy data, and validate the DB."""
    ensure_dirs()
    # A staged restore is applied before legacy migration, engine initialization,
    # saved settings, themes, or any database-dependent UI is loaded.
    apply_pending_restore()
    migrate_legacy_data()
    _ensure_engine_bound_to_config()
    return init_db()


def initialize_account_context_data() -> Path | None:
    """Prepare an already selected account/workspace storage root.

    This intentionally does not run legacy shared-database migration. Existing
    Teacher Hub data remains preserved and inaccessible until a future explicit
    owner-claim/import flow.
    """

    ensure_dirs()
    apply_pending_restore()
    _ensure_engine_bound_to_config()
    return init_db()


def startup_error_message(error: BaseException) -> str:
    return (
        "تعذر فتح قاعدة بيانات Teacher Hub بأمان.\n\n"
        f"المسار: {config.DB_PATH}\n"
        f"التفاصيل: {error}\n\n"
        "لم يحذف البرنامج البيانات الأصلية. أغلق التطبيق وكل عملياته قبل "
        "أي استعادة يدوية، واحتفظ بملف قاعدة البيانات الحالي.\n"
        f"مجلد النسخ الاحتياطية: {config.BACKUPS_DIR}\n"
        "استعد نسخة سليمة أو اطلب الدعم قبل محاولة التشغيل مرة أخرى."
    )


def log_startup_failure(error: BaseException) -> Path:
    config.ensure_dirs()
    target = config.LOGS_DIR / "startup_error.log"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{datetime.now().isoformat(timespec='seconds')} startup failure\n")
        traceback.print_exception(type(error), error, error.__traceback__, file=handle)
    return target

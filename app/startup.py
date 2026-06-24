from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from app.config import (
    BACKUPS_DIR,
    DB_PATH,
    LOGS_DIR,
    ensure_dirs,
    migrate_legacy_data,
)
from app.db.engine import init_db


def initialize_application_data() -> Path | None:
    """Prepare stable user storage, migrate legacy data, and validate the DB."""
    ensure_dirs()
    migrate_legacy_data()
    return init_db()


def startup_error_message(error: BaseException) -> str:
    return (
        "تعذر فتح قاعدة بيانات Teacher Hub بأمان.\n\n"
        f"المسار: {DB_PATH}\n"
        f"التفاصيل: {error}\n\n"
        "لم يحذف البرنامج البيانات الأصلية. أغلق التطبيق وكل عملياته قبل "
        "أي استعادة يدوية، واحتفظ بملف قاعدة البيانات الحالي.\n"
        f"مجلد النسخ الاحتياطية: {BACKUPS_DIR}\n"
        "استعد نسخة سليمة أو اطلب الدعم قبل محاولة التشغيل مرة أخرى."
    )


def log_startup_failure(error: BaseException) -> Path:
    ensure_dirs()
    target = LOGS_DIR / "startup_error.log"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{datetime.now().isoformat(timespec='seconds')} startup failure\n")
        traceback.print_exception(type(error), error, error.__traceback__, file=handle)
    return target

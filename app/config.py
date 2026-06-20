from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXPORTS_DIR = BASE_DIR / "exports"
INVOICES_DIR = EXPORTS_DIR / "invoices"
RESOURCES_DIR = BASE_DIR / "app" / "resources"
FONTS_DIR = RESOURCES_DIR / "fonts"
ICONS_DIR = RESOURCES_DIR / "icons"
STYLES_DIR = BASE_DIR / "app" / "ui" / "styles"

DB_PATH = DATA_DIR / "teacher.db"
DB_URL = f"sqlite:///{DB_PATH}"

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


def ensure_dirs():
    for p in (DATA_DIR, EXPORTS_DIR, INVOICES_DIR, FONTS_DIR, ICONS_DIR):
        p.mkdir(parents=True, exist_ok=True)

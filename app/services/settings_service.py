import json
from typing import Any

from sqlalchemy.exc import OperationalError

from app.db.engine import discard_session, get_session, session_scope
from app.db.models import Setting


DEFAULT_TEMPLATES = [
    {
        "name": "تذكير قبل الحصة",
        "text": "السلام عليكم {name}، تذكير بموعد حصتنا اليوم الساعة {time}. رابط Zoom: {zoom}",
    },
    {
        "name": "إرسال فيديو / تسجيل",
        "text": "السلام عليكم {name}، هذا رابط تسجيل/شرح الحصة. ذاكر جيداً وأي سؤال أنا موجود.",
    },
    {
        "name": "استحقاق الدفع",
        "text": "السلام عليكم {name}، أرفقت لكم فاتورة الدورة الحالية ({sessions} حصص - {amount}). شاكر لكم.",
    },
    {
        "name": "اعتذار عن حصة",
        "text": "السلام عليكم {name}، أعتذر أن حصة اليوم ستتأجل. سأحدد لكم ميعاد تعويضي قريباً.",
    },
    {
        "name": "تهنئة بالنجاح",
        "text": "ألف مبروك {name}! أداء ممتاز في آخر حصص. استمر بنفس المستوى.",
    },
]


def get_setting(key: str, default: Any = None) -> Any:
    try:
        s = get_session()
        row = s.get(Setting, key)
    except OperationalError:
        discard_session()
        return default
    if row is None:
        return default
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        return row.value


def set_setting(key: str, value: Any) -> None:
    with session_scope() as s:
        row = s.get(Setting, key)
        serialized = json.dumps(value, ensure_ascii=False)
        if row is None:
            s.add(Setting(key=key, value=serialized))
        else:
            row.value = serialized


def get_theme() -> str:
    return get_setting("theme", "light") or "light"


def set_theme(theme: str) -> None:
    set_setting("theme", theme)


def get_templates() -> list:
    templates = get_setting("whatsapp_templates")
    if not templates:
        return list(DEFAULT_TEMPLATES)
    return templates


def save_templates(templates: list) -> None:
    set_setting("whatsapp_templates", templates)


def get_notifications_enabled() -> bool:
    v = get_setting("notifications_enabled", True)
    return bool(v) if v is not None else True


def set_notifications_enabled(enabled: bool) -> None:
    set_setting("notifications_enabled", bool(enabled))


def get_notification_minutes() -> int:
    v = get_setting("notification_minutes", 10)
    try:
        return int(v) if v is not None else 10
    except (TypeError, ValueError):
        return 10


def set_notification_minutes(minutes: int) -> None:
    set_setting("notification_minutes", int(minutes))


# ---- Invoice WhatsApp template ----

DEFAULT_INVOICE_MESSAGE = (
    "السلام عليكم ورحمة الله وبركاته {name} 🌿\n\n"
    "تم إصدار فاتورة الدورة الحالية:\n"
    "• عدد الحصص: {sessions}\n"
    "• عدد الفيديوهات: {videos}\n"
    "• إجمالي المبلغ: {amount}\n"
    "• تاريخ الإصدار: {date}\n\n"
    "سأرفق الفاتورة بصيغة PDF في الرسالة التالية.\n"
    "شاكر لكم تعاونكم 🤍"
)


def get_invoice_message_template() -> str:
    v = get_setting("invoice_whatsapp_template")
    return v if v else DEFAULT_INVOICE_MESSAGE


def set_invoice_message_template(text: str) -> None:
    set_setting("invoice_whatsapp_template", text)

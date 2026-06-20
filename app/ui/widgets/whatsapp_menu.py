from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from datetime import datetime

from app.config import CURRENCY
from app.services.settings_service import get_templates
from app.services.launcher import open_whatsapp, open_whatsapp_group, render_template
from app.services.student_service import counted_sessions, get_day_schedules

_PY_WD_MAP = {0: "MON", 1: "TUE", 2: "WED", 3: "THU", 4: "FRI", 5: "SAT", 6: "SUN"}


def _format_time_ar(hhmm: str) -> str:
    """Convert 'HH:MM' (24h) to Arabic 12h format with ص/م."""
    if not hhmm:
        return "-"
    try:
        h, m = hhmm.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return hhmm
    period = "م" if h >= 12 else "ص"
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{m:02d} {period}"


def _has_group(student) -> bool:
    link = (getattr(student, "whatsapp_group_link", "") or "").strip()
    return bool(link)


def _add_target_options(submenu: QMenu, student, message: str, on_error):
    """Add «إلى الطالب» and «إلى المجموعة» (if available) actions to a submenu."""
    to_student = submenu.addAction("👤  إلى الطالب")
    to_student.triggered.connect(
        lambda _=False, m=message: _send_to_student(student, m, on_error)
    )
    if _has_group(student):
        to_group = submenu.addAction("👥  إلى مجموعة الطالب")
        to_group.triggered.connect(
            lambda _=False, m=message: _send_to_group(student, m, on_error)
        )
    else:
        disabled = submenu.addAction("👥  لا توجد مجموعة محفوظة لهذا الطالب")
        disabled.setEnabled(False)


def build_whatsapp_menu(parent, student, on_error=None):
    menu = QMenu(parent)
    menu.setLayoutDirection(Qt.RightToLeft)

    has_phone = bool(student.phone)
    has_group = _has_group(student)

    if not has_phone and not has_group:
        act = menu.addAction("لا يوجد رقم واتساب ولا مجموعة لهذا الطالب")
        act.setEnabled(False)
        return menu

    sessions_count = counted_sessions(student.id)
    amount = sessions_count * (student.price_per_session or 0)

    # Today's specific time(s)
    day_sched = get_day_schedules(student)
    today_code = _PY_WD_MAP[datetime.now().weekday()]
    today_times = day_sched.get(today_code) or []
    if not today_times:
        today_time = student.session_time or ""
    elif len(today_times) == 1:
        today_time = today_times[0]
    else:
        today_time = today_times[0]

    # Quick "open empty chat" — student only (groups need a prefilled message anyway)
    if has_phone:
        open_act = menu.addAction("💬  فتح محادثة فارغة (للطالب)")
        open_act.triggered.connect(
            lambda: _send_to_student(student, "", on_error)
        )
    if has_group:
        open_group_act = menu.addAction("👥  فتح المجموعة (بدون رسالة)")
        open_group_act.triggered.connect(
            lambda: _send_to_group(student, "", on_error)
        )
    menu.addSeparator()

    templates = get_templates()
    if not templates:
        empty = menu.addAction("لا توجد قوالب")
        empty.setEnabled(False)
        return menu

    # If multiple times today, group templates by time first
    if len(today_times) > 1:
        for tpl in templates:
            tpl_name = tpl.get("name", "قالب")
            tpl_text = tpl.get("text", "")
            tpl_menu = menu.addMenu(f"✉  {tpl_name}")
            for t in today_times:
                time_sub = tpl_menu.addMenu(_format_time_ar(t))
                rendered = render_template(
                    tpl_text,
                    name=student.name,
                    time=t,
                    zoom=student.zoom_link or "",
                    sessions=sessions_count,
                    amount=f"{amount:.0f} {CURRENCY}",
                )
                _add_target_options(time_sub, student, rendered, on_error)
    else:
        for tpl in templates:
            name = tpl.get("name", "قالب")
            text = tpl.get("text", "")
            tpl_menu = menu.addMenu(f"✉  {name}")
            rendered = render_template(
                text,
                name=student.name,
                time=today_time,
                zoom=student.zoom_link or "",
                sessions=sessions_count,
                amount=f"{amount:.0f} {CURRENCY}",
            )
            _add_target_options(tpl_menu, student, rendered, on_error)

    return menu


def _send_to_student(student, message, on_error):
    if not student.phone:
        if on_error:
            on_error("لا يوجد رقم واتساب لهذا الطالب.")
        return
    if not open_whatsapp(student.phone, message):
        if on_error:
            on_error("تعذر فتح واتساب. تأكد من تثبيت WhatsApp Desktop.")


def _send_to_group(student, message, on_error):
    link = (getattr(student, "whatsapp_group_link", "") or "").strip()
    if not link:
        if on_error:
            on_error("لا توجد مجموعة محفوظة لهذا الطالب.")
        return
    if not open_whatsapp_group(link, message):
        if on_error:
            on_error("تعذر فتح المجموعة. تحقق من الرابط أو تثبيت واتساب.")
        return
    if on_error and message:
        # Use on_error as a generic notification channel — caller typically shows toast
        on_error("تم نسخ الرسالة. ألصقها داخل المجموعة بـ Ctrl+V ثم اضغط إرسال.")


# Backwards compat: keep _send name in case other modules reference it
_send = _send_to_student

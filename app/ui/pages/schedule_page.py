"""Weekly calendar view (8 AM → 12 midnight) for the teacher's schedule.

Days are columns (RTL: السبت on the far right), hours are rows.
Students appear as vibrant colored cards in their slot. Clicking a card
opens the quick-actions popup (Zoom, record session, WhatsApp, undo).
"""
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QSize, QPoint
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QPushButton, QScrollArea, QMessageBox, QCheckBox
)

from app.config import WEEKDAYS, WEEKDAY_AR
from app.services.student_service import (
    counted_sessions_map, list_students, get_student, counted_sessions,
    get_day_schedules, get_custom_fields
)
from app.services.session_service import add_session, undo_last_session
from app.services.launcher import open_zoom
from app.ui.helpers.icons import icon, ICONS
from app.ui.helpers.shadow import add_shadow
from app.ui.helpers.theme import theme_manager
from app.ui.helpers.time_format import format_hour_ar, format_time_ar
from app.ui.widgets.whatsapp_menu import build_whatsapp_menu


PYTHON_WEEKDAY_MAP = {
    0: "MON", 1: "TUE", 2: "WED", 3: "THU", 4: "FRI", 5: "SAT", 6: "SUN"
}

# Calendar range: 8 AM through 11 PM inclusive (user asked 8 صباحاً → 12 ليلاً)
CAL_START_HOUR = 8
CAL_END_HOUR = 23  # 11 PM; "12 ليلاً" = midnight = end of the 11-PM row

# Vibrant palette cycled by student id for clear, distinguishable colors.
STUDENT_PALETTE = [
    ("#6366F1", "#4338CA"),  # indigo
    ("#10B981", "#047857"),  # emerald
    ("#F59E0B", "#B45309"),  # amber
    ("#EC4899", "#BE185D"),  # pink
    ("#06B6D4", "#0E7490"),  # cyan
    ("#8B5CF6", "#6D28D9"),  # violet
    ("#F43F5E", "#BE123C"),  # rose
    ("#14B8A6", "#0F766E"),  # teal
    ("#3B82F6", "#1D4ED8"),  # blue
    ("#84CC16", "#4D7C0F"),  # lime
]

DUE_COLOR = "#DC2626"
DUE_DARK = "#991B1B"


def today_code() -> str:
    return PYTHON_WEEKDAY_MAP[datetime.now().weekday()]


def student_colors(student_id: int, is_due: bool):
    if is_due:
        return DUE_COLOR, DUE_DARK
    return STUDENT_PALETTE[student_id % len(STUDENT_PALETTE)]


# ---------------------------------------------------------------------------
# Student block — one event card in the calendar
# ---------------------------------------------------------------------------

class StudentBlock(QFrame):
    clicked = Signal(int)  # emits student_id

    def __init__(
        self, student, is_today: bool, time_24h: str = "", count: int | None = None
    ):
        super().__init__()
        self.student_id = student.id
        self.student_name = student.name
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(56)
        self.setMaximumHeight(90)

        if count is None:
            count = counted_sessions(student.id)
        cycle = student.sessions_per_cycle or 8
        is_due = count >= cycle
        bg, border = student_colors(student.id, is_due)

        border_width = 3 if is_today else 1
        self.setStyleSheet(
            f"StudentBlock {{"
            f"  background-color: {bg};"
            f"  border: {border_width}px solid {border};"
            f"  border-radius: 10px;"
            f"}}"
            f"StudentBlock:hover {{"
            f"  background-color: {border};"
            f"}}"
            f"QLabel {{ background: transparent; color: white; }}"
        )
        add_shadow(self, blur=12, y_offset=2, opacity=28)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(2)

        name_lbl = QLabel(student.name)
        name_lbl.setStyleSheet("color: white; font-weight: 700; font-size: 13px;")
        name_lbl.setWordWrap(False)
        v.addWidget(name_lbl)

        display_time = time_24h or student.session_time or ""
        meta_lbl = QLabel(
            f"{format_time_ar(display_time)}  •  {count}/{cycle}"
            + ("  ⚠" if is_due else "")
        )
        meta_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.92); font-size: 11px; font-weight: 500;"
        )
        v.addWidget(meta_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.student_id)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Quick actions popup (compact menu with Zoom / Record / WhatsApp / Undo)
# ---------------------------------------------------------------------------

class QuickActionsPopup(QFrame):
    data_changed = Signal()

    def __init__(self, student, parent=None):
        super().__init__(parent, Qt.Popup)
        self.student = student
        self.setObjectName("Card")
        popup_bg = theme_manager.card_bg()
        popup_border = theme_manager.divider_color()
        self.setStyleSheet(
            f"QFrame#Card {{ background: {popup_bg}; border-radius: 14px; "
            f"border: 1px solid {popup_border}; }}"
        )
        add_shadow(self, blur=26, y_offset=6, opacity=55)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        count = counted_sessions(self.student.id)
        cycle = self.student.sessions_per_cycle or 8
        is_due = count >= cycle

        text_col = theme_manager.text_color()
        muted_col = theme_manager.muted_text_color()

        # Header
        header = QHBoxLayout()
        name = QLabel(self.student.name)
        name.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {text_col};")
        header.addWidget(name, 1)
        if is_due:
            badge = QLabel("مستحق")
            badge.setObjectName("BadgeDue")
            header.addWidget(badge)
        layout.addLayout(header)

        # Show today's specific time(s) if available, else legacy
        day_sched = get_day_schedules(self.student)
        today_c = today_code()
        today_times = day_sched.get(today_c) or []
        if today_times:
            # Multiple times: join with ، ; single time: just show it
            times_display = " ، ".join(format_time_ar(t) for t in today_times)
        else:
            legacy = self.student.session_time or ""
            times_display = format_time_ar(legacy) if legacy else "-"
        meta = QLabel(
            f"الوقت: <b>{times_display}</b>  •  "
            f"العداد: <b style='color:{DUE_COLOR if is_due else '#6366F1'}'>{count}/{cycle}</b>"
        )
        meta.setStyleSheet(f"color: {muted_col}; font-size: 12px;")
        layout.addWidget(meta)

        # -- Optional quick-view fields (built-ins + custom) --
        info_lines: list[tuple[str, str]] = []
        zoom_name = getattr(self.student, "zoom_link_name", "") or ""
        if zoom_name:
            info_lines.append(("رابط Zoom", zoom_name))
        parent_phone = getattr(self.student, "parent_phone", "") or ""
        if parent_phone:
            info_lines.append(("ولي الأمر", parent_phone))
        for cf in get_custom_fields(self.student):
            if cf.get("show_in_popup") and cf.get("value"):
                info_lines.append((cf.get("label", ""), cf.get("value", "")))

        if info_lines:
            info_frame = QFrame()
            info_frame.setStyleSheet(
                f"QFrame {{ background: {theme_manager.subtle_bg()}; "
                f"border: 1px solid {theme_manager.divider_color()}; "
                "border-radius: 8px; }}"
            )
            iv = QVBoxLayout(info_frame)
            iv.setContentsMargins(10, 8, 10, 8)
            iv.setSpacing(3)
            for lbl, val in info_lines:
                line = QLabel(
                    f"<span style='color:{muted_col}; font-size:11px;'>{lbl}:</span> "
                    f"<b style='color:{text_col}; font-size:12px;'>{val}</b>"
                )
                line.setTextInteractionFlags(Qt.TextSelectableByMouse)
                line.setWordWrap(True)
                iv.addWidget(line)
            layout.addWidget(info_frame)

        # Actions
        zoom_btn = QPushButton("  بدء الحصة (Zoom)")
        zoom_btn.setIcon(icon(ICONS["zoom"], color="#FFFFFF"))
        zoom_btn.setIconSize(QSize(16, 16))
        zoom_btn.clicked.connect(self._start_zoom)
        layout.addWidget(zoom_btn)

        self.free_check = QCheckBox("احتساب هذه الحصة كـ «مجانية»")
        self.free_check.setToolTip("حصة تجريبية/تعويضية لا تُحسب في الدورة")
        layout.addWidget(self.free_check)

        plus_btn = QPushButton("  تسجيل الحصة (+1)")
        plus_btn.setObjectName("SuccessBtn")
        plus_btn.setIcon(icon(ICONS["add"], color="#FFFFFF"))
        plus_btn.setIconSize(QSize(16, 16))
        plus_btn.clicked.connect(self._record_session)
        layout.addWidget(plus_btn)

        wa_btn = QPushButton("  إرسال واتساب")
        wa_btn.setObjectName("WhatsAppBtn")
        wa_btn.setIcon(icon(ICONS["whatsapp"], color="#FFFFFF"))
        wa_btn.setIconSize(QSize(16, 16))
        wa_btn.clicked.connect(self._whatsapp)
        layout.addWidget(wa_btn)

        self.undo_btn = QPushButton("  تراجع عن آخر حصة")
        self.undo_btn.setObjectName("GhostBtn")
        self.undo_btn.setIcon(icon(ICONS["undo"], color=theme_manager.icon_color()))
        self.undo_btn.setIconSize(QSize(14, 14))
        self.undo_btn.clicked.connect(self._undo)
        layout.addWidget(self.undo_btn)

        self.setFixedWidth(300)

    def _start_zoom(self):
        if not self.student.zoom_link:
            QMessageBox.information(self, "لا يوجد رابط", "رابط Zoom غير مسجل لهذا الطالب.")
            return
        if not open_zoom(self.student.zoom_link):
            QMessageBox.warning(self, "خطأ", "تعذر فتح رابط Zoom.")

    def _record_session(self):
        from app.services.student_service import counted_sessions
        from app.services.launcher import open_whatsapp

        is_free = self.free_check.isChecked()
        cycle = int(self.student.sessions_per_cycle or 8)
        prior_paid = counted_sessions(self.student.id)

        # Cap warning: refuse to add a paid session past the cycle without renewal
        if not is_free and prior_paid >= cycle:
            QMessageBox.warning(
                self,
                "اكتملت الباقة",
                f"الطالب أكمل {cycle} حصص بالفعل (باقة منتهية).\n"
                "أصدر فاتورة وصفِّر العداد قبل تسجيل حصة جديدة، "
                "أو فعّل «حصة مجانية» للتعويض/التجريب.",
            )
            return

        add_session(self.student.id, is_free=is_free)
        self.data_changed.emit()

        # Send WhatsApp confirmation only for paid sessions (skip free/trial)
        if not is_free and self.student.phone:
            new_count = prior_paid + 1
            remaining = max(0, cycle - new_count)
            if new_count >= cycle:
                msg = (
                    f"تم تسجيل الحصة رقم {new_count} للطالب {self.student.name}. "
                    f"اكتملت الباقة ({cycle}/{cycle}). "
                    "الرجاء تجديد الاشتراك للاستمرار."
                )
            else:
                msg = (
                    f"تم تسجيل الحصة رقم {new_count} للطالب {self.student.name}. "
                    f"المتبقي {remaining} حصص من الباقة."
                )
            try:
                open_whatsapp(self.student.phone, msg)
            except Exception:
                pass  # Don't block the flow if WA fails

        self.close()

    def _undo(self):
        if undo_last_session(self.student.id):
            self.data_changed.emit()
        self.close()

    def _whatsapp(self):
        menu = build_whatsapp_menu(
            self, self.student,
            on_error=lambda msg: QMessageBox.warning(self, "خطأ", msg)
        )
        pos = self.mapToGlobal(QPoint(0, self.height()))
        menu.exec(pos)
        self.close()


# ---------------------------------------------------------------------------
# Schedule page — weekly calendar grid
# ---------------------------------------------------------------------------

class SchedulePage(QWidget):
    data_changed = Signal()

    def __init__(self, auto_refresh: bool = True):
        super().__init__()
        self.setObjectName("CentralSurface")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(6)

        # Title row
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        title = QLabel("جدول الأسبوع")
        title.setObjectName("PageTitle")
        top_row.addWidget(title)
        self.count_badge = QLabel()
        self.count_badge.setObjectName("BadgePending")
        top_row.addWidget(self.count_badge)
        top_row.addStretch(1)
        root.addLayout(top_row)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        root.addWidget(self.subtitle)

        root.addSpacing(10)

        # Legend
        legend = QHBoxLayout()
        legend.setSpacing(14)
        legend.addWidget(self._legend_chip("#6366F1", "حصة عادية"))
        legend.addWidget(self._legend_chip("#10B981", "حصة أخرى"))
        legend.addWidget(self._legend_chip(DUE_COLOR, "مستحق الدفع"))
        is_dark = theme_manager.is_dark()
        today_chip_bg = "#78350F" if is_dark else "#FEF3C7"
        today_chip_fg = "#FDE68A" if is_dark else "#92400E"
        legend.addWidget(self._legend_chip(today_chip_bg, "اليوم الحالي", text_color=today_chip_fg))
        legend.addStretch(1)
        root.addLayout(legend)

        root.addSpacing(8)

        # Scrollable calendar grid
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; } "
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        root.addWidget(self.scroll, 1)

        if auto_refresh:
            self.refresh()

    def _legend_chip(self, color: str, label: str, text_color: str = "#FFFFFF") -> QWidget:
        w = QFrame()
        w.setStyleSheet(
            f"QFrame {{ background-color: {color}; border-radius: 10px; }} "
            f"QLabel {{ color: {text_color}; font-size: 11px; font-weight: 600; background: transparent; }}"
        )
        hl = QHBoxLayout(w)
        hl.setContentsMargins(10, 4, 10, 4)
        lbl = QLabel(label)
        hl.addWidget(lbl)
        return w

    def refresh(self):
        # Replace grid container entirely
        grid_container = QWidget()
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(4)

        today = today_code()
        day_codes = [c for c, _ in WEEKDAYS]

        # --- Header row (day labels) ---
        is_dark = theme_manager.is_dark()
        corner_bg = "#1E293B" if is_dark else "#F1F5F9"
        corner_fg = "#CBD5E1" if is_dark else "#475569"
        day_bg = "#312E81" if is_dark else "#E0E7FF"
        day_fg = "#C7D2FE" if is_dark else "#3730A3"
        today_bg = "#78350F" if is_dark else "#FEF3C7"
        today_fg = "#FDE68A" if is_dark else "#92400E"
        today_border = "#F59E0B"

        # Column 0 = time labels; columns 1..7 = days
        corner = QLabel("الوقت")
        corner.setAlignment(Qt.AlignCenter)
        corner.setStyleSheet(
            f"background: {corner_bg}; color: {corner_fg}; "
            "font-weight: 700; border-radius: 8px; padding: 10px 6px;"
        )
        grid.addWidget(corner, 0, 0)

        for col, (code, label) in enumerate(WEEKDAYS, start=1):
            is_today = code == today
            lbl = QLabel(label + ("  • اليوم" if is_today else ""))
            lbl.setAlignment(Qt.AlignCenter)
            if is_today:
                lbl.setStyleSheet(
                    f"background: {today_bg}; color: {today_fg}; "
                    "font-weight: 800; border-radius: 8px; padding: 10px 6px; "
                    f"border: 2px solid {today_border};"
                )
            else:
                lbl.setStyleSheet(
                    f"background: {day_bg}; color: {day_fg}; "
                    "font-weight: 700; border-radius: 8px; padding: 10px 6px;"
                )
            grid.addWidget(lbl, 0, col)

        # --- Hour rows + empty slot cells ---
        # We track each cell as a QWidget with a vertical layout so multiple
        # students at the same hour can stack neatly.
        hours = list(range(CAL_START_HOUR, CAL_END_HOUR + 1))
        cell_containers: dict[tuple[int, int], QVBoxLayout] = {}

        hour_bg = "#172033" if is_dark else "#F8FAFC"
        hour_fg = "#CBD5E1" if is_dark else "#334155"
        cell_bg = "#0F172A" if is_dark else "#FAFAFA"
        cell_border = "#334155" if is_dark else "#E2E8F0"
        cell_today_bg = "#1F1605" if is_dark else "#FFFBEB"
        cell_today_border = "#B45309" if is_dark else "#FDE68A"

        for row_idx, hour in enumerate(hours, start=1):
            time_lbl = QLabel(format_hour_ar(hour))
            time_lbl.setAlignment(Qt.AlignCenter)
            time_lbl.setStyleSheet(
                f"background: {hour_bg}; color: {hour_fg}; "
                "font-weight: 700; border-radius: 8px; padding: 10px 4px; "
                "font-size: 12px;"
            )
            time_lbl.setMinimumWidth(70)
            grid.addWidget(time_lbl, row_idx, 0)

            for col, code in enumerate(day_codes, start=1):
                cell = QFrame()
                is_today_col = code == today
                if is_today_col:
                    cell.setStyleSheet(
                        f"QFrame {{ background-color: {cell_today_bg}; "
                        f"border: 1px dashed {cell_today_border}; border-radius: 8px; }}"
                    )
                else:
                    cell.setStyleSheet(
                        f"QFrame {{ background-color: {cell_bg}; "
                        f"border: 1px dashed {cell_border}; border-radius: 8px; }}"
                    )
                cell.setMinimumHeight(70)

                cv = QVBoxLayout(cell)
                cv.setContentsMargins(4, 4, 4, 4)
                cv.setSpacing(3)
                cv.addStretch(1)
                grid.addWidget(cell, row_idx, col)
                cell_containers[(hour, col)] = cv

        # Column stretch: time column narrow, day columns equal
        grid.setColumnStretch(0, 0)
        for c in range(1, 8):
            grid.setColumnStretch(c, 1)

        # --- Place students ---
        students = list_students(active_only=True)
        session_counts = counted_sessions_map()
        placed = 0
        today_count = 0
        for s in students:
            day_sched = get_day_schedules(s)
            if not day_sched:
                continue

            for day_code, day_times in day_sched.items():
                # day_times is now a list of times
                if not day_times:
                    continue

                for day_time in day_times:
                    try:
                        h, _m = day_time.split(":")
                        h = int(h)
                    except ValueError:
                        continue
                    if h < CAL_START_HOUR or h > CAL_END_HOUR:
                        continue
                    try:
                        col = day_codes.index(day_code) + 1
                    except ValueError:
                        continue
                    cv = cell_containers.get((h, col))
                    if cv is None:
                        continue
                    block = StudentBlock(
                        s,
                        is_today=(day_code == today),
                        time_24h=day_time,
                        count=session_counts.get(s.id, 0),
                    )
                    block.clicked.connect(self._open_actions)
                    # Insert before the stretch
                    cv.insertWidget(cv.count() - 1, block)
                    placed += 1
                    if day_code == today:
                        today_count += 1

        # Subtitle + badge
        today_ar = WEEKDAY_AR.get(today, today)
        self.subtitle.setText(
            f"اليوم {today_ar}  •  {today_count} حصة اليوم  •  {placed} حصة في الأسبوع كله"
        )
        self.count_badge.setText(f"{today_count} اليوم")

        self.scroll.setWidget(grid_container)

    def _open_actions(self, student_id: int):
        s = get_student(student_id)
        if not s:
            return
        popup = QuickActionsPopup(s, parent=self)
        popup.data_changed.connect(self._on_change)
        # Position popup near the cursor
        from PySide6.QtGui import QCursor
        pos = QCursor.pos()
        popup.move(pos.x() - 280, pos.y())
        popup.show()
        # Keep a reference so it isn't GC'd before interaction
        self._active_popup = popup

    def _on_change(self):
        self.data_changed.emit()

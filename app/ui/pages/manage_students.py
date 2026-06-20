from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QLineEdit,
    QDoubleSpinBox, QSpinBox, QTextEdit, QLabel, QFrame, QComboBox,
    QWidget, QScrollArea, QCheckBox
)

from app.config import WEEKDAYS, WEEKDAY_AR, CURRENCY
from app.services.student_service import (
    list_students, create_student, update_student, delete_student,
    get_student, get_day_schedules, get_custom_fields
)
from app.ui.helpers.icons import icon, ICONS
from app.ui.helpers.shadow import add_shadow
from app.ui.helpers.theme import theme_manager
from app.ui.helpers.worker import run_in_background


# ---------------------------------------------------------------------------
# Reusable small components
# ---------------------------------------------------------------------------

def make_field_row(icon_key: str, label_text: str, widget: QWidget) -> QWidget:
    """Label (with FA icon) on the right, editor on the left — RTL form row."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(10)

    # Label block (right side in RTL)
    label_block = QWidget()
    lh = QHBoxLayout(label_block)
    lh.setContentsMargins(0, 0, 0, 0)
    lh.setSpacing(6)
    ic = QLabel()
    ic.setPixmap(icon(ICONS[icon_key], color=theme_manager.accent_color()).pixmap(16, 16))
    lh.addWidget(ic)
    lbl = QLabel(label_text)
    lbl.setObjectName("FormLabel")
    lh.addWidget(lbl)
    lh.addStretch(1)
    label_block.setFixedWidth(150)
    h.addWidget(label_block)

    h.addWidget(widget, 1)
    return row


class ArabicTimePicker(QWidget):
    """Hour (1-12) : Minute (every 5) + AM/PM combos — stored as 24h string.

    Uses QComboBox dropdowns (not QSpinBox) so users always see the picker
    arrow and can't get stuck on a default value.
    """

    def __init__(self, initial_24h: str = "17:00"):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.hour = QComboBox()
        for h in range(1, 13):
            self.hour.addItem(str(h), h)
        self.hour.setFixedWidth(80)

        colon = QLabel(":")
        colon.setAlignment(Qt.AlignCenter)
        colon.setStyleSheet("font-size: 16px; font-weight: 700; color: #475569;")

        self.minute = QComboBox()
        for m in range(0, 60, 5):
            self.minute.addItem(f"{m:02d}", m)
        self.minute.setFixedWidth(80)

        self.period = QComboBox()
        self.period.addItem("صباحاً", "AM")
        self.period.addItem("مساءً", "PM")
        self.period.setFixedWidth(120)

        layout.addWidget(self.hour)
        layout.addWidget(colon)
        layout.addWidget(self.minute)
        layout.addSpacing(6)
        layout.addWidget(self.period)
        layout.addStretch(1)

        self.set_time_24h(initial_24h)

    def set_time_24h(self, hhmm: str):
        try:
            h, m = hhmm.split(":")
            h, m = int(h), int(m)
        except (ValueError, AttributeError):
            h, m = 17, 0
        period = "PM" if h >= 12 else "AM"
        h12 = h % 12
        if h12 == 0:
            h12 = 12
        # Snap minute to nearest 5
        m_snapped = (m // 5) * 5
        self.hour.setCurrentIndex(h12 - 1)
        idx_m = max(0, min(11, m_snapped // 5))
        self.minute.setCurrentIndex(idx_m)
        self.period.setCurrentIndex(1 if period == "PM" else 0)

    def time_24h(self) -> str:
        h = self.hour.currentData()
        m = self.minute.currentData()
        ampm = self.period.currentData()
        if ampm == "AM":
            if h == 12:
                h = 0
        else:  # PM
            if h != 12:
                h += 12
        return f"{h:02d}:{m:02d}"


class DayChip(QPushButton):
    """Toggleable day button — looks like a pill."""
    def __init__(self, code: str, label: str):
        super().__init__(label)
        self.code = code
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self.setMinimumWidth(90)
        is_dark = theme_manager.is_dark() if hasattr(theme_manager, "is_dark") else False
        if is_dark:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #1E293B;
                    color: #CBD5E1;
                    border: 1.5px solid #334155;
                    border-radius: 18px;
                    padding: 0 14px;
                    font-size: 13px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    border-color: #818CF8;
                    color: #C7D2FE;
                }
                QPushButton:checked {
                    background-color: #6366F1;
                    color: #FFFFFF;
                    border: 1.5px solid #6366F1;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #F1F5F9;
                    color: #475569;
                    border: 1.5px solid #E2E8F0;
                    border-radius: 18px;
                    padding: 0 14px;
                    font-size: 13px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    border-color: #A5B4FC;
                    color: #4338CA;
                }
                QPushButton:checked {
                    background-color: #6366F1;
                    color: #FFFFFF;
                    border: 1.5px solid #6366F1;
                }
            """)


class _TimeRow(QWidget):
    """One time picker + delete button in a compact horizontal row."""
    remove_requested = Signal(object)

    def __init__(self, preset_24h: str | None = None):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self.picker = ArabicTimePicker(preset_24h or "17:00")
        if preset_24h:
            self.picker.set_time_24h(preset_24h)

        self.delete_btn = QPushButton("✕")
        self.delete_btn.setFixedSize(28, 28)
        self.delete_btn.setToolTip("حذف هذا الوقت")
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94A3B8;
                border: 1px solid #E2E8F0;
                border-radius: 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #FEE2E2;
                color: #DC2626;
                border-color: #FCA5A5;
            }
        """)
        self.delete_btn.clicked.connect(lambda: self.remove_requested.emit(self))

        h.addWidget(self.picker, 1)
        h.addWidget(self.delete_btn)


class DayScheduleRow(QWidget):
    """Compact row: [DayChip] | [time rows stacked] [+ small add button].

    RTL layout: DayChip on the right, time pickers on the left.
    Supports multiple sessions per day.
    """

    def __init__(self, code: str, label: str, initial_times: list[str] | None = None):
        super().__init__()
        self.code = code
        self.day_label_ar = label

        # Main horizontal layout: [DayChip] [right side with times + add btn]
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        # Left side: DayChip (fixed width, top-aligned)
        self.chip = DayChip(code, label)
        self.chip.setMinimumWidth(100)
        self.chip.setMaximumWidth(110)
        self.chip.toggled.connect(self._on_toggled)

        # Right side: times stack + inline add-time button
        right_side = QVBoxLayout()
        right_side.setContentsMargins(0, 0, 0, 0)
        right_side.setSpacing(4)

        # Times container
        self.times_container = QWidget()
        self.times_layout = QVBoxLayout(self.times_container)
        self.times_layout.setContentsMargins(0, 0, 0, 0)
        self.times_layout.setSpacing(4)

        # Compact add time button (small, subtle link-style)
        self.add_time_btn = QPushButton("+ إضافة وقت آخر")
        self.add_time_btn.setCursor(Qt.PointingHandCursor)
        self.add_time_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #6366F1;
                border: none;
                padding: 2px 6px;
                font-size: 11px;
                font-weight: 600;
                text-align: right;
            }
            QPushButton:hover {
                color: #4338CA;
                text-decoration: underline;
            }
            QPushButton:disabled {
                color: #CBD5E1;
            }
        """)
        self.add_time_btn.clicked.connect(lambda: self._add_time_row(None))

        right_side.addWidget(self.times_container)
        right_side.addWidget(self.add_time_btn, 0, Qt.AlignRight)

        # Assemble: chip on right (RTL first), times on left
        outer.addWidget(self.chip, 0, Qt.AlignTop)
        outer.addLayout(right_side, 1)

        # Time rows list
        self.time_rows: list[_TimeRow] = []
        if initial_times:
            self.chip.setChecked(True)
            for t in initial_times:
                self._add_time_row(t)
        else:
            self._add_time_row(None)
            self._set_enabled(False)

    def _add_time_row(self, preset: str | None = None):
        row = _TimeRow(preset)
        row.remove_requested.connect(self._remove_row)
        self.time_rows.append(row)
        self.times_layout.addWidget(row)

    def _remove_row(self, row: _TimeRow):
        if len(self.time_rows) <= 1:
            return  # Keep at least one row
        self.time_rows.remove(row)
        row.deleteLater()

    def _on_toggled(self, checked: bool):
        self._set_enabled(checked)

    def _set_enabled(self, on: bool):
        self.times_container.setEnabled(on)
        self.add_time_btn.setEnabled(on)

    def is_checked(self) -> bool:
        return self.chip.isChecked()

    def set_checked(self, checked: bool):
        self.chip.setChecked(checked)
        self._on_toggled(checked)

    def get_times(self) -> list[str]:
        """Return list of HH:MM times, deduplicated and sorted."""
        raw = [r.picker.time_24h() for r in self.time_rows if r.picker.time_24h()]
        return sorted(set(raw))

    def set_times(self, times: list[str]):
        """Replace all time rows with the given list of times."""
        # Remove all existing rows
        for r in self.time_rows:
            r.deleteLater()
        self.time_rows.clear()
        # Add new rows
        if times:
            for t in times:
                self._add_time_row(t)
        else:
            # Always keep one row, even if empty
            self._add_time_row(None)

    def set_time_24h(self, hhmm: str):
        """Backward-compat: set a single time (used by legacy loaders)."""
        self.set_times([hhmm] if hhmm else [])

    def validate(self) -> tuple[bool, str]:
        """Validate this day's times. Return (is_valid, error_message)."""
        if not self.is_checked():
            return True, ""
        raw = [r.picker.time_24h() for r in self.time_rows if r.picker.time_24h()]
        if len(raw) != len(set(raw)):
            return False, f"وقت مكرر في يوم {self.day_label_ar}"
        if not raw:
            return False, f"يجب تحديد وقت واحد على الأقل ليوم {self.day_label_ar}"
        return True, ""


class CustomFieldRow(QWidget):
    """One row in the "Custom fields" section.

    Layout (RTL):
      [ اسم الخانة ]  [ القيمة ]  [☑ إظهار في الـ popup]  [🗑]

    Empty label rows are silently discarded on save.
    """

    removed = None  # set in __init__

    def __init__(self, label: str = "", value: str = "", show_in_popup: bool = False):
        super().__init__()

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        self.label_edit = QLineEdit(label)
        self.label_edit.setPlaceholderText("اسم الخانة  (مثل: رقم احتياطي)")
        self.label_edit.setFixedWidth(180)

        self.value_edit = QLineEdit(value)
        self.value_edit.setPlaceholderText("القيمة")

        self.popup_check = QCheckBox("إظهار في الـ popup")
        self.popup_check.setChecked(bool(show_in_popup))
        self.popup_check.setToolTip(
            "عند التفعيل، ستظهر هذه الخانة عند الضغط على الطالب في الجدول الأسبوعي"
        )

        self.remove_btn = QPushButton()
        self.remove_btn.setIcon(icon(ICONS["delete"], color="#DC2626"))
        self.remove_btn.setIconSize(QSize(14, 14))
        self.remove_btn.setFixedSize(32, 32)
        self.remove_btn.setToolTip("حذف هذه الخانة")
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #E2E8F0;"
            "  border-radius: 8px; }"
            "QPushButton:hover { background: #FEE2E2; border-color: #FCA5A5; }"
        )
        self.remove_btn.clicked.connect(self._emit_removed)

        h.addWidget(self.label_edit)
        h.addWidget(self.value_edit, 1)
        h.addWidget(self.popup_check)
        h.addWidget(self.remove_btn)

        self._remove_callback = None

    def set_remove_callback(self, cb):
        self._remove_callback = cb

    def _emit_removed(self):
        if self._remove_callback:
            self._remove_callback(self)

    def to_dict(self) -> dict:
        return {
            "label": self.label_edit.text().strip(),
            "value": self.value_edit.text().strip(),
            "show_in_popup": self.popup_check.isChecked(),
        }


# ---------------------------------------------------------------------------
# StudentFormDialog
# ---------------------------------------------------------------------------

class StudentFormDialog(QDialog):
    def __init__(self, student_id=None, parent=None):
        super().__init__(parent)
        self.student_id = student_id
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("تعديل طالب" if student_id else "إضافة طالب جديد")
        self.resize(680, 760)
        self.setMinimumSize(620, 620)
        self._build()
        if student_id:
            self._load()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Header ----
        header = QFrame()
        header.setStyleSheet(
            "background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #6366F1, stop:1 #818CF8);"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 20, 24, 20)
        hl.setSpacing(12)

        ic = QLabel()
        ic.setPixmap(icon(ICONS["user"], color="#FFFFFF").pixmap(28, 28))
        hl.addWidget(ic)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("تعديل بيانات الطالب" if self.student_id else "إضافة طالب جديد")
        title.setStyleSheet("color: #FFFFFF; font-size: 20px; font-weight: 700;")
        sub = QLabel("املأ البيانات بدقة لضمان ظهور الحصص في الجدول")
        sub.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 12px;")
        titles.addWidget(title)
        titles.addWidget(sub)
        hl.addLayout(titles, 1)

        root.addWidget(header)

        # ---- Scrollable form body ----
        surface = theme_manager.surface_bg()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {surface}; border: none; }}")

        body = QWidget()
        body.setStyleSheet(f"background: {surface};")
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(24, 20, 24, 20)
        body_l.setSpacing(16)

        # === Card 1: Basic info ===
        card1 = self._make_card("البيانات الأساسية")
        card1_inner = card1.layout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("مثال: أحمد محمد")
        card1_inner.addWidget(make_field_row("user", "اسم الطالب:", self.name_edit))

        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("201234567890  (الكود الدولي بدون +)")
        card1_inner.addWidget(make_field_row("phone", "رقم الواتساب:", self.phone_edit))

        self.parent_phone_edit = QLineEdit()
        self.parent_phone_edit.setPlaceholderText("اختياري — رقم ولي الأمر (بصيغة دولية)")
        card1_inner.addWidget(make_field_row("phone", "رقم ولي الأمر:", self.parent_phone_edit))

        body_l.addWidget(card1)

        # === Card 2: Session details ===
        card2 = self._make_card("تفاصيل الحصص")
        card2_inner = card2.layout()

        self.zoom_edit = QLineEdit()
        self.zoom_edit.setPlaceholderText("https://zoom.us/j/...")
        card2_inner.addWidget(make_field_row("link", "رابط Zoom:", self.zoom_edit))

        self.zoom_name_edit = QLineEdit()
        self.zoom_name_edit.setPlaceholderText("اختياري — مثل: «غرفة الفيزياء» أو اسم الرابط")
        card2_inner.addWidget(make_field_row("link", "اسم رابط Zoom:", self.zoom_name_edit))

        self.wa_group_edit = QLineEdit()
        self.wa_group_edit.setPlaceholderText("اختياري — https://chat.whatsapp.com/XXXXXXXXX")
        card2_inner.addWidget(make_field_row("whatsapp", "رابط مجموعة واتساب:", self.wa_group_edit))

        self.price_edit = QDoubleSpinBox()
        self.price_edit.setRange(0, 100000)
        self.price_edit.setDecimals(2)
        self.price_edit.setSuffix(f" {CURRENCY}")
        self.price_edit.setAlignment(Qt.AlignLeft)
        card2_inner.addWidget(make_field_row("money", "سعر الحصة:", self.price_edit))

        self.cycle_edit = QSpinBox()
        self.cycle_edit.setRange(1, 100)
        self.cycle_edit.setValue(8)
        self.cycle_edit.setSuffix("  حصة")
        self.cycle_edit.setAlignment(Qt.AlignLeft)
        card2_inner.addWidget(make_field_row("cycle", "عدد الحصص/دورة:", self.cycle_edit))

        body_l.addWidget(card2)

        # === Card 3: Weekly days — each with its own time ===
        card3 = self._make_card("أيام الحصص الأسبوعية")
        card3_inner = card3.layout()

        hint = QLabel(
            "فعّل أيام الحصص — ولكل يوم وقته الخاص (مثال: السبت 6م، الإثنين 7م، الثلاثاء 9م)."
        )
        hint.setObjectName("PageSubtitle")
        hint.setWordWrap(True)
        card3_inner.addWidget(hint)
        card3_inner.addSpacing(4)

        self.day_rows = {}
        for code, label in WEEKDAYS:
            row = DayScheduleRow(code, label)
            self.day_rows[code] = row
            card3_inner.addWidget(row)

        body_l.addWidget(card3)

        # === Card 3.5: Custom (user-defined) fields ===
        card_cf = self._make_card("خانات إضافية (تُعرّفها بنفسك)")
        cf_inner = card_cf.layout()

        cf_hint = QLabel(
            "أضف أي خانات تريدها (رقم احتياطي، اسم المادة، عنوان، …). "
            "فعّل «إظهار في الـ popup» لتظهر الخانة عند الضغط على الطالب في الجدول."
        )
        cf_hint.setObjectName("PageSubtitle")
        cf_hint.setWordWrap(True)
        cf_inner.addWidget(cf_hint)

        # Container for rows
        self.cf_rows_container = QWidget()
        self.cf_rows_layout = QVBoxLayout(self.cf_rows_container)
        self.cf_rows_layout.setContentsMargins(0, 4, 0, 4)
        self.cf_rows_layout.setSpacing(6)
        cf_inner.addWidget(self.cf_rows_container)

        self.custom_field_rows = []

        add_cf_btn = QPushButton("  إضافة خانة جديدة")
        add_cf_btn.setObjectName("GhostBtn")
        add_cf_btn.setIcon(icon(ICONS["add"], color=theme_manager.accent_color()))
        add_cf_btn.setIconSize(QSize(14, 14))
        add_cf_btn.setCursor(Qt.PointingHandCursor)
        add_cf_btn.clicked.connect(lambda: self._add_custom_row())
        cf_inner.addWidget(add_cf_btn, 0, Qt.AlignRight)

        body_l.addWidget(card_cf)

        # === Card 4: Notes ===
        card4 = self._make_card("ملاحظات")
        card4_inner = card4.layout()
        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(90)
        self.notes_edit.setPlaceholderText("أي ملاحظة عن الطالب — المستوى الدراسي، التفضيلات، إلخ")
        card4_inner.addWidget(self.notes_edit)
        body_l.addWidget(card4)

        body_l.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ---- Footer: buttons (explicit inline styling so they always render) ----
        is_dark = theme_manager.is_dark()
        footer_bg = theme_manager.card_bg()
        divider = theme_manager.divider_color()
        footer = QFrame()
        footer.setFixedHeight(72)
        footer.setStyleSheet(
            f"QFrame {{ background-color: {footer_bg}; border-top: 1px solid {divider}; }}"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(24, 14, 24, 14)
        fl.setSpacing(12)

        cancel_txt = theme_manager.muted_text_color()
        cancel_btn = QPushButton("  إلغاء")
        cancel_btn.setObjectName("GhostBtn")
        cancel_btn.setIcon(icon(ICONS["cancel"], color=cancel_txt))
        cancel_btn.setIconSize(QSize(16, 16))
        cancel_btn.setMinimumSize(130, 42)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        if is_dark:
            cancel_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #1E293B; color: #CBD5E1;"
                "  border: 1.5px solid #334155; border-radius: 10px;"
                "  padding: 0 18px; font-size: 13px; font-weight: 600;"
                "}"
                "QPushButton:hover { background-color: #273449; border-color: #64748B; color: #F1F5F9; }"
            )
        else:
            cancel_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #FFFFFF; color: #475569;"
                "  border: 1.5px solid #CBD5E1; border-radius: 10px;"
                "  padding: 0 18px; font-size: 13px; font-weight: 600;"
                "}"
                "QPushButton:hover { background-color: #F1F5F9; border-color: #94A3B8; color: #0F172A; }"
            )
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("  حفظ البيانات")
        save_btn.setObjectName("SuccessBtn")
        save_btn.setIcon(icon(ICONS["save"], color="#FFFFFF"))
        save_btn.setIconSize(QSize(16, 16))
        save_btn.setMinimumSize(170, 42)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setDefault(True)
        save_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #10B981; color: #FFFFFF;"
            "  border: none; border-radius: 10px;"
            "  padding: 0 22px; font-size: 14px; font-weight: 700;"
            "}"
            "QPushButton:hover { background-color: #059669; }"
            "QPushButton:pressed { background-color: #047857; }"
        )
        save_btn.clicked.connect(self._save)

        fl.addStretch(1)
        fl.addWidget(cancel_btn)
        fl.addWidget(save_btn)
        root.addWidget(footer)

    def _add_custom_row(self, label: str = "", value: str = "", show_in_popup: bool = False):
        row = CustomFieldRow(label=label, value=value, show_in_popup=show_in_popup)
        row.set_remove_callback(self._remove_custom_row)
        self.custom_field_rows.append(row)
        self.cf_rows_layout.addWidget(row)

    def _remove_custom_row(self, row: "CustomFieldRow"):
        if row in self.custom_field_rows:
            self.custom_field_rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _make_card(self, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        add_shadow(card, blur=14, y_offset=2, opacity=14)
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        lbl = QLabel(title)
        lbl.setObjectName("SectionTitle")
        v.addWidget(lbl)
        sep = QFrame()
        sep.setObjectName("HDivider")
        sep.setFrameShape(QFrame.HLine)
        v.addWidget(sep)
        return card

    def _load(self):
        s = get_student(self.student_id)
        if not s:
            return
        self.name_edit.setText(s.name)
        self.phone_edit.setText(s.phone or "")
        self.parent_phone_edit.setText(getattr(s, "parent_phone", "") or "")
        self.zoom_edit.setText(s.zoom_link or "")
        self.zoom_name_edit.setText(getattr(s, "zoom_link_name", "") or "")
        self.wa_group_edit.setText(getattr(s, "whatsapp_group_link", "") or "")
        self.price_edit.setValue(s.price_per_session or 0)
        self.cycle_edit.setValue(s.sessions_per_cycle or 8)
        self.notes_edit.setPlainText(s.notes or "")

        # Load per-day schedules (handles legacy fallback inside helper)
        # day_sched[code] is now a list of times (can be multiple per day)
        day_sched = get_day_schedules(s)
        legacy_time = (s.session_time or "17:00").strip() or "17:00"
        for code, row in self.day_rows.items():
            if code in day_sched:
                times = day_sched[code] or [legacy_time]
                row.set_times(times)
                row.set_checked(True)
            else:
                row.set_times([legacy_time])
                row.set_checked(False)

        # Load custom fields
        for cf in get_custom_fields(s):
            self._add_custom_row(
                label=cf.get("label", ""),
                value=cf.get("value", ""),
                show_in_popup=bool(cf.get("show_in_popup", False)),
            )

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "تنبيه", "اسم الطالب مطلوب.")
            return

        # Validate all day rows first
        for row in self.day_rows.values():
            ok, err = row.validate()
            if not ok:
                QMessageBox.warning(self, "خطأ", err)
                return

        day_schedules = {
            code: row.get_times()
            for code, row in self.day_rows.items()
            if row.is_checked()
        }

        # Legacy compatibility: session_time = first day's first time
        legacy_session_time = ""
        if day_schedules:
            # Use first day in WEEKDAYS order that is checked
            for code, _ in WEEKDAYS:
                if code in day_schedules:
                    times = day_schedules[code]
                    legacy_session_time = times[0] if times else ""
                    break

        # Collect custom fields (drop rows with empty label)
        custom_fields = []
        for row in self.custom_field_rows:
            d = row.to_dict()
            if d["label"]:
                custom_fields.append(d)

        payload = dict(
            name=name,
            phone=self.phone_edit.text().strip(),
            parent_phone=self.parent_phone_edit.text().strip(),
            zoom_link=self.zoom_edit.text().strip(),
            zoom_link_name=self.zoom_name_edit.text().strip(),
            whatsapp_group_link=self.wa_group_edit.text().strip(),
            price_per_session=self.price_edit.value(),
            sessions_per_cycle=self.cycle_edit.value(),
            day_schedules=day_schedules,
            session_time=legacy_session_time,
            notes=self.notes_edit.toPlainText().strip(),
            custom_fields=custom_fields,
        )
        try:
            if self.student_id:
                update_student(self.student_id, **payload)
            else:
                create_student(**payload)
        except ValueError as e:
            QMessageBox.warning(self, "بيانات غير صالحة", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "خطأ", f"تعذر حفظ بيانات الطالب:\n{e}")
            return
        self.accept()


# ---------------------------------------------------------------------------
# ManageStudentsDialog
# ---------------------------------------------------------------------------

class ManageStudentsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("إدارة الطلاب")
        self.resize(1000, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # ---- Title + actions row ----
        title_row = QHBoxLayout()
        title_row.setSpacing(10)

        title = QLabel("إدارة الطلاب")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)

        self.count_badge = QLabel()
        self.count_badge.setObjectName("BadgePending")
        title_row.addWidget(self.count_badge)

        title_row.addStretch(1)

        add_btn = QPushButton("  إضافة طالب")
        add_btn.setObjectName("SuccessBtn")
        add_btn.setIcon(icon(ICONS["add"], color="#FFFFFF"))
        add_btn.setIconSize(QSize(14, 14))
        add_btn.clicked.connect(self._add)
        title_row.addWidget(add_btn)

        edit_btn = QPushButton("  تعديل")
        edit_btn.setObjectName("GhostBtn")
        edit_btn.setIcon(icon(ICONS["edit"], color="#475569"))
        edit_btn.setIconSize(QSize(14, 14))
        edit_btn.clicked.connect(self._edit)
        title_row.addWidget(edit_btn)

        deactivate_btn = QPushButton("  إيقاف تفعيل")
        deactivate_btn.setObjectName("GhostBtn")
        deactivate_btn.setIcon(icon(ICONS["archive"], color="#D97706"))
        deactivate_btn.setIconSize(QSize(14, 14))
        deactivate_btn.setToolTip("إخفاء الطالب من القوائم مع الاحتفاظ ببياناته")
        deactivate_btn.clicked.connect(self._deactivate)
        title_row.addWidget(deactivate_btn)

        delete_btn = QPushButton("  حذف نهائي")
        delete_btn.setObjectName("DangerBtn")
        delete_btn.setIcon(icon(ICONS["delete"], color="#FFFFFF"))
        delete_btn.setIconSize(QSize(14, 14))
        delete_btn.setToolTip("حذف الطالب وكل حصصه وفيديوهاته بشكل دائم — لا يمكن التراجع")
        delete_btn.clicked.connect(self._delete)
        title_row.addWidget(delete_btn)

        export_btn = QPushButton("  تصدير Excel")
        export_btn.setObjectName("GhostBtn")
        export_btn.setIconSize(QSize(14, 14))
        export_btn.setToolTip("تصدير جميع الطلاب وبياناتهم إلى ملف Excel")
        export_btn.clicked.connect(self._export_xlsx)
        title_row.addWidget(export_btn)
        self.export_btn = export_btn

        layout.addLayout(title_row)

        subtitle = QLabel("نقرتان على صف لتعديل الطالب، أو استخدم الأزرار بالأعلى.")
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(subtitle)

        layout.addSpacing(4)

        # ---- Table ----
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["الاسم", "الهاتف", "السعر", "حصص/دورة", "الأيام", "الأوقات", "الحالة"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        # Use cellDoubleClicked (emits row,col ints) so we can safely ignore args
        self.table.cellDoubleClicked.connect(lambda r, c: self._edit())
        # Also allow single-click to select the row even in the status column
        self.table.cellClicked.connect(lambda r, c: self.table.setCurrentCell(r, 0))
        layout.addWidget(self.table, 1)

        # ---- Bottom close button ----
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("إغلاق")
        close.setObjectName("GhostBtn")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)

        self._refresh()

    # -- Time formatting helper (24h -> Arabic 12h) --
    @staticmethod
    def _format_time_ar(hhmm: str) -> str:
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

    def _refresh(self):
        students = list_students(active_only=False)
        self.table.setRowCount(len(students))
        active_count = sum(1 for s in students if s.is_active)
        self.count_badge.setText(f"{active_count} / {len(students)} طالب")

        for row, s in enumerate(students):
            day_sched = get_day_schedules(s)
            # Sort by WEEKDAYS order for consistent display
            ordered_codes = [c for c, _ in WEEKDAYS if c in day_sched]

            if ordered_codes:
                days_str = "، ".join(WEEKDAY_AR.get(c, c) for c in ordered_codes)
                # Compact list of each day @ its times (now a list)
                parts = []
                for c in ordered_codes:
                    times = day_sched[c]
                    times_ar = " ، ".join(self._format_time_ar(t) for t in times)
                    parts.append(f"{WEEKDAY_AR.get(c, c)}: {times_ar}")
                times_str = " • ".join(parts)
            else:
                days_str = "-"
                times_str = "-"

            name_item = QTableWidgetItem(s.name)
            name_item.setData(Qt.UserRole, s.id)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(s.phone or "-"))
            price_item = QTableWidgetItem(f"{s.price_per_session:.2f} {CURRENCY}")
            price_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, price_item)
            cycle_item = QTableWidgetItem(str(s.sessions_per_cycle))
            cycle_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, cycle_item)
            self.table.setItem(row, 4, QTableWidgetItem(days_str))
            time_item = QTableWidgetItem(times_str)
            time_item.setTextAlignment(Qt.AlignCenter)
            time_item.setToolTip(times_str)
            self.table.setItem(row, 5, time_item)

            # Status as a styled item (NOT a cell widget) — cell widgets
            # swallow clicks and break row selection on that column.
            status_item = QTableWidgetItem("● نشط" if s.is_active else "● موقوف")
            status_item.setTextAlignment(Qt.AlignCenter)
            if s.is_active:
                status_item.setForeground(QBrush(QColor("#16A34A")))  # green
            else:
                status_item.setForeground(QBrush(QColor("#94A3B8")))  # gray
            f = QFont()
            f.setBold(True)
            status_item.setFont(f)
            self.table.setItem(row, 6, status_item)

    def _selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _add(self):
        dlg = StudentFormDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._refresh()

    def _edit(self):
        sid = self._selected_id()
        if sid is None:
            QMessageBox.information(self, "تنبيه", "اختر طالباً من الجدول.")
            return
        dlg = StudentFormDialog(student_id=sid, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._refresh()

    def _deactivate(self):
        sid = self._selected_id()
        if sid is None:
            QMessageBox.information(self, "تنبيه", "اختر طالباً أولاً.")
            return
        if QMessageBox.question(
            self, "تأكيد الإيقاف",
            "سيُخفى هذا الطالب من القوائم مع الاحتفاظ بكامل سجلاته.\n"
            "يمكنك إعادة تفعيله لاحقاً. متابعة؟"
        ) == QMessageBox.Yes:
            delete_student(sid, soft=True)
            self._refresh()

    def _delete(self):
        sid = self._selected_id()
        if sid is None:
            QMessageBox.information(self, "تنبيه", "اختر طالباً أولاً.")
            return
        # Get the student name for the confirmation text
        row = self.table.currentRow()
        name = self.table.item(row, 0).text() if row >= 0 and self.table.item(row, 0) else "هذا الطالب"
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Warning)
        confirm.setWindowTitle("حذف نهائي")
        confirm.setText(
            f"<b>حذف «{name}» نهائياً؟</b>"
        )
        confirm.setInformativeText(
            "سيتم حذف الطالب وكل حصصه وفيديوهاته من قاعدة البيانات.\n"
            "هذا الإجراء <b>لا يمكن التراجع عنه</b>.\n\n"
            "إن كنت تريد فقط إخفاءه مع الاحتفاظ بالسجلات، استخدم «إيقاف تفعيل»."
        )
        yes_btn = confirm.addButton("حذف نهائي", QMessageBox.DestructiveRole)
        confirm.addButton("إلغاء", QMessageBox.RejectRole)
        confirm.setDefaultButton(confirm.buttons()[-1])  # default = cancel
        confirm.exec()
        if confirm.clickedButton() is yes_btn:
            delete_student(sid, soft=False)
            self._refresh()

    def _export_xlsx(self):
        from app.services.excel_service import export_students_xlsx

        self.export_btn.setEnabled(False)
        run_in_background(
            self,
            export_students_xlsx,
            on_result=self._on_xlsx_exported,
            on_error=self._on_xlsx_error,
            on_finished=lambda: self.export_btn.setEnabled(True),
        )

    def _on_xlsx_exported(self, path):
        from app.services.launcher import open_path

        info = QMessageBox(self)
        info.setIcon(QMessageBox.Information)
        info.setWindowTitle("تم التصدير")
        info.setText("تم تصدير الطلاب إلى Excel بنجاح.")
        info.setInformativeText(str(path))
        open_btn = info.addButton("فتح الملف", QMessageBox.AcceptRole)
        folder_btn = info.addButton("فتح المجلد", QMessageBox.ActionRole)
        info.addButton("إغلاق", QMessageBox.RejectRole)
        info.exec()
        clicked = info.clickedButton()
        if clicked is open_btn:
            open_path(path)
        elif clicked is folder_btn:
            open_path(path.parent)

    def _on_xlsx_error(self, error):
        QMessageBox.warning(self, "خطأ", f"تعذر تصدير الملف:\n{error}")

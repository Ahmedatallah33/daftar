from datetime import datetime
import subprocess
import sys

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QGridLayout, QPushButton,
    QMessageBox, QCheckBox, QDialog, QLineEdit
)

from app.config import CURRENCY
from app.services.billing_service import monthly_stats, reset_all_activity
from app.ui.helpers.icons import icon, ICONS
from app.ui.helpers.shadow import add_shadow
from app.ui.helpers.theme import theme_manager


ARABIC_MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"
]


class StatCard(QFrame):
    def __init__(self, label: str, value: str = "—", color: str = "#6366F1", icon: str = "💼"):
        super().__init__()
        self.setObjectName("StatCard")
        add_shadow(self, blur=16, y_offset=3, opacity=18)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(8)

        top = QHBoxLayout()
        ic = QLabel(icon)
        ic.setStyleSheet(f"font-size: 24px; color: {color};")
        top.addWidget(ic)
        top.addStretch(1)
        layout.addLayout(top)

        lbl = QLabel(label)
        lbl.setObjectName("StudentMeta")
        self.val = QLabel(value)
        self.val.setStyleSheet(f"color: {color}; font-size: 26px; font-weight: 700;")
        layout.addWidget(lbl)
        layout.addWidget(self.val)

    def set_value(self, text: str):
        self.val.setText(text)


class ReportsPage(QWidget):
    data_changed = Signal()

    def __init__(self, auto_refresh: bool = True):
        super().__init__()
        self.setObjectName("CentralSurface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(6)

        title = QLabel("التقارير والإحصائيات")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        sub = QLabel("نظرة على أدائك المالي وعدد الحصص خلال الشهر المحدد.")
        sub.setObjectName("PageSubtitle")
        layout.addWidget(sub)

        layout.addSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.year_combo = QComboBox()
        current_year = datetime.now().year
        for y in range(current_year - 3, current_year + 2):
            self.year_combo.addItem(str(y), y)
        self.year_combo.setCurrentText(str(current_year))
        self.year_combo.setFixedWidth(110)

        self.month_combo = QComboBox()
        for i, m in enumerate(ARABIC_MONTHS, start=1):
            self.month_combo.addItem(m, i)
        self.month_combo.setCurrentIndex(datetime.now().month - 1)
        self.month_combo.setFixedWidth(140)

        self.year_combo.currentIndexChanged.connect(self.refresh)
        self.month_combo.currentIndexChanged.connect(self.refresh)

        controls.addWidget(QLabel("السنة:"))
        controls.addWidget(self.year_combo)
        controls.addSpacing(8)
        controls.addWidget(QLabel("الشهر:"))
        controls.addWidget(self.month_combo)
        controls.addStretch(1)
        layout.addLayout(controls)

        layout.addSpacing(14)

        self.income_card = StatCard("الدخل الإجمالي", color="#6366F1", icon="💰")
        self.paid_card = StatCard("المحصّل", color="#10B981", icon="✓")
        self.pending_card = StatCard("بانتظار التحصيل", color="#F59E0B", icon="⏳")
        self.sessions_card = StatCard("عدد الحصص", color="#0EA5E9", icon="📚")
        self.avg_card = StatCard("متوسط سعر الحصة", color="#7C3AED", icon="📊")

        self.stats_grid = QGridLayout()
        self.stats_grid.setSpacing(14)
        self.stats_grid.addWidget(self.income_card, 0, 0)
        self.stats_grid.addWidget(self.paid_card, 0, 1)
        self.stats_grid.addWidget(self.pending_card, 0, 2)
        self.stats_grid.addWidget(self.sessions_card, 1, 0)
        self.stats_grid.addWidget(self.avg_card, 1, 1)
        layout.addLayout(self.stats_grid)

        layout.addSpacing(18)

        section = QLabel("أكثر الطلاب نشاطاً هذا الشهر")
        section.setObjectName("SectionTitle")
        layout.addWidget(section)

        layout.addSpacing(6)

        self.top_table = QTableWidget(0, 3)
        self.top_table.setHorizontalHeaderLabels(["#", "اسم الطالب", "عدد الحصص"])
        self.top_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.top_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.top_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.top_table.setAlternatingRowColors(True)
        self.top_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.top_table.verticalHeader().setVisible(False)
        self.top_table.verticalHeader().setDefaultSectionSize(44)
        layout.addWidget(self.top_table, 1)

        # ---- Danger zone: restart/reset ----
        layout.addSpacing(14)
        layout.addWidget(self._build_danger_zone())

        if auto_refresh:
            self.refresh()

    def _build_danger_zone(self) -> QFrame:
        card = QFrame()
        card.setObjectName("DangerZone")
        card.setStyleSheet(
            "QFrame#DangerZone {"
            "  background-color: #FEF2F2;"
            "  border: 1.5px solid #FECACA;"
            "  border-radius: 14px;"
            "}"
        )
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # Left side: description
        left = QVBoxLayout()
        left.setSpacing(2)

        title = QLabel("⚠ منطقة الخطر — إعادة التشغيل")
        title.setStyleSheet(
            "color: #991B1B; font-size: 15px; font-weight: 800; background: transparent;"
        )
        left.addWidget(title)

        desc = QLabel(
            "تصفير شامل لبدء فترة تعليمية جديدة من الصفر. "
            "يتم إنشاء <b>نسخة محلية متحقّق منها لقاعدة SQLite</b> قبل أي حذف. "
            "يجب حماية النسخة نفسها؛ فهي محلية ولا تشمل ملفات PDF أو XLSX المُصدَّرة. "
            "يمكنك استعادة بيانات القاعدة من مجلد "
            "<code>%LOCALAPPDATA%\\TeacherHub\\backups</code>."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            "color: #7F1D1D; font-size: 12px; background: transparent;"
        )
        left.addWidget(desc)

        layout.addLayout(left, 1)

        # Right side: button
        btn = QPushButton("  إعادة تشغيل / تصفير شامل")
        btn.setObjectName("DangerBtn")
        btn.setIcon(icon(ICONS["undo"], color="#FFFFFF"))
        btn.setIconSize(QSize(16, 16))
        btn.setMinimumSize(210, 44)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #DC2626; color: #FFFFFF;"
            "  border: none; border-radius: 10px;"
            "  padding: 0 18px; font-size: 13px; font-weight: 700;"
            "}"
            "QPushButton:hover { background-color: #B91C1C; }"
            "QPushButton:pressed { background-color: #991B1B; }"
        )
        btn.clicked.connect(self._restart)
        layout.addWidget(btn, 0, Qt.AlignVCenter)

        return card

    def _restart(self):
        dlg = RestartConfirmDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        keep_students = dlg.keep_students()
        try:
            backup_path, counts = reset_all_activity(keep_students=keep_students)
        except Exception as e:
            QMessageBox.critical(self, "خطأ", f"تعذّرت إعادة التشغيل:\n{e}")
            return

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("تمّت إعادة التشغيل")
        removed = (
            f"• الحصص المحذوفة: <b>{counts['sessions']}</b><br>"
            f"• الفيديوهات المحذوفة: <b>{counts['videos']}</b><br>"
            f"• الفواتير المحذوفة: <b>{counts['invoices']}</b><br>"
        )
        if not keep_students:
            removed += f"• الطلاب المحذوفون: <b>{counts['students']}</b><br>"
        msg.setText("<b>تمّ تصفير الفترة بنجاح.</b>")
        msg.setInformativeText(
            f"{removed}<br>"
            f"تم حفظ نسخة احتياطية على:<br>"
            f"<code style='color:#6366F1'>{backup_path}</code>"
        )
        open_btn = msg.addButton("فتح مجلد النسخ الاحتياطية", QMessageBox.ActionRole)
        msg.addButton("حسناً", QMessageBox.AcceptRole)
        msg.exec()
        if msg.clickedButton() is open_btn:
            self._open_folder(backup_path.parent)

        self.data_changed.emit()

    @staticmethod
    def _open_folder(path):
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    def refresh(self):
        year = self.year_combo.currentData()
        month = self.month_combo.currentData()
        if year is None or month is None:
            return

        stats = monthly_stats(int(year), int(month))

        self.income_card.set_value(f"{stats['income']:.0f} {CURRENCY}")
        self.paid_card.set_value(f"{stats['paid_income']:.0f} {CURRENCY}")
        self.pending_card.set_value(f"{stats['pending_income']:.0f} {CURRENCY}")
        self.sessions_card.set_value(str(stats['sessions']))
        avg = (stats['income'] / stats['sessions']) if stats['sessions'] else 0
        self.avg_card.set_value(f"{avg:.0f} {CURRENCY}")

        top = stats["top_students"]
        self.top_table.setRowCount(len(top))
        for row, (name, count) in enumerate(top):
            rank = QTableWidgetItem(f"#{row+1}")
            rank.setTextAlignment(Qt.AlignCenter)
            self.top_table.setItem(row, 0, rank)
            self.top_table.setItem(row, 1, QTableWidgetItem(name))
            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignCenter)
            self.top_table.setItem(row, 2, count_item)


# ---------------------------------------------------------------------------
# Restart confirmation dialog — requires typing a confirmation phrase.
# ---------------------------------------------------------------------------

CONFIRM_PHRASE = "إعادة تشغيل"


class RestartConfirmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("تأكيد إعادة التشغيل")
        self.setMinimumWidth(520)

        is_dark = theme_manager.is_dark()
        card_bg = theme_manager.card_bg()
        text_col = theme_manager.text_color()
        muted_col = theme_manager.muted_text_color()
        border_col = theme_manager.input_border()
        hover_bg = "#273449" if is_dark else "#F1F5F9"
        input_bg = "#172033" if is_dark else "#FFFFFF"
        warn_title_col = "#F87171" if is_dark else "#991B1B"
        warn_body_col = "#CBD5E1" if is_dark else "#334155"
        code_bg = "#78350F" if is_dark else "#FEF3C7"
        code_fg = "#FDE68A" if is_dark else "#92400E"

        self.setStyleSheet(f"QDialog {{ background: {card_bg}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("⚠ إعادة تشغيل شاملة")
        title.setStyleSheet(
            f"color: {warn_title_col}; font-size: 18px; font-weight: 800;"
        )
        layout.addWidget(title)

        warn = QLabel(
            "<b>ستُحذف جميع الحصص والفيديوهات والفواتير</b> من قاعدة البيانات. "
            "هذا الإجراء يُستخدم عادةً لبدء فصل دراسي جديد أو فترة مالية جديدة.<br><br>"
            "يتم <b>حفظ نسخة أمان محلية لبيانات القاعدة</b> قبل التنفيذ، ويمكن "
            "استعادتها لاحقاً. هذه النسخة لا تشمل ملفات PDF أو XLSX المُصدَّرة."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color: {warn_body_col}; font-size: 13px; line-height: 1.5;")
        layout.addWidget(warn)

        # Option: keep students vs full wipe
        self.keep_check = QCheckBox("الاحتفاظ ببيانات الطلاب (اسم/هاتف/سعر/جدول) — موصى به")
        self.keep_check.setChecked(True)
        self.keep_check.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {text_col};"
        )
        layout.addWidget(self.keep_check)

        # Type-to-confirm
        prompt = QLabel(
            f"للتأكيد، اكتب العبارة التالية حرفياً:<br>"
            f"<code style='background:{code_bg}; padding:2px 6px; border-radius:4px;"
            f" color:{code_fg}; font-size:14px;'>{CONFIRM_PHRASE}</code>"
        )
        prompt.setStyleSheet(f"color: {muted_col}; font-size: 12px;")
        layout.addWidget(prompt)

        self.phrase_edit = QLineEdit()
        self.phrase_edit.setPlaceholderText("اكتب العبارة هنا…")
        self.phrase_edit.textChanged.connect(self._check_phrase)
        self.phrase_edit.setStyleSheet(
            f"QLineEdit {{ padding: 10px 12px; border: 1.5px solid {border_col}; "
            f"background: {input_bg}; color: {text_col}; "
            "border-radius: 8px; font-size: 14px; }"
            "QLineEdit:focus { border: 2px solid #DC2626; padding: 9px 11px; }"
        )
        layout.addWidget(self.phrase_edit)

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch(1)

        cancel = QPushButton("إلغاء")
        cancel.setMinimumSize(120, 40)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton {{ background:{card_bg}; color:{muted_col}; "
            f"border: 1.5px solid {border_col}; border-radius: 10px; "
            "padding: 0 18px; font-size: 13px; font-weight: 600; }"
            f"QPushButton:hover {{ background: {hover_bg}; color: {text_col}; }}"
        )
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)

        self.confirm_btn = QPushButton("نعم، أعِد التشغيل")
        self.confirm_btn.setMinimumSize(180, 40)
        self.confirm_btn.setCursor(Qt.PointingHandCursor)
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.setStyleSheet(
            "QPushButton { background:#DC2626; color:#FFFFFF; "
            "border: none; border-radius: 10px; "
            "padding: 0 22px; font-size: 13px; font-weight: 700; }"
            "QPushButton:hover { background: #B91C1C; }"
            "QPushButton:disabled { background:#FCA5A5; color:#FFFFFF; }"
        )
        self.confirm_btn.clicked.connect(self.accept)
        btns.addWidget(self.confirm_btn)

        layout.addLayout(btns)

    def _check_phrase(self, text: str):
        self.confirm_btn.setEnabled(text.strip() == CONFIRM_PHRASE)

    def keep_students(self) -> bool:
        return self.keep_check.isChecked()

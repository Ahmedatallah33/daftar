from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QWidget, QFormLayout, QDateTimeEdit, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QCheckBox, QPlainTextEdit,
    QFrame, QGridLayout
)

from app.config import CURRENCY
from app.services.student_service import (
    get_student, counted_sessions, counted_videos, get_custom_fields
)
from app.services.session_service import (
    add_session, add_video, list_sessions, list_videos,
    delete_session, delete_video, update_session
)
from app.ui.helpers.theme import theme_manager


class StudentDetailDialog(QDialog):
    data_changed = Signal()

    def __init__(self, student_id: int, parent=None):
        super().__init__(parent)
        self.student_id = student_id
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(820, 640)
        self._build()
        self.refresh()

    def _build(self):
        student = get_student(self.student_id)
        if not student:
            self.close()
            return
        self.setWindowTitle(f"تفاصيل الطالب — {student.name}")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QLabel(student.name)
        header.setObjectName("PageTitle")
        root.addWidget(header)

        self.summary = QLabel()
        self.summary.setObjectName("PageSubtitle")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        # Contact + custom info card (collapsible-feeling block)
        self.info_frame = QFrame()
        self.info_frame.setStyleSheet(
            f"QFrame {{ background: {theme_manager.subtle_bg()}; "
            f"border: 1px solid {theme_manager.divider_color()}; "
            "border-radius: 10px; }}"
        )
        self.info_layout = QGridLayout(self.info_frame)
        self.info_layout.setContentsMargins(14, 10, 14, 10)
        self.info_layout.setHorizontalSpacing(18)
        self.info_layout.setVerticalSpacing(6)
        root.addWidget(self.info_frame)
        self._populate_info(student)

        tabs = QTabWidget()
        tabs.addTab(self._build_add_session_tab(), "➕  إضافة حصة")
        tabs.addTab(self._build_sessions_tab(), "📚  سجل الحصص")
        tabs.addTab(self._build_videos_tab(), "🎬  الفيديوهات")
        root.addWidget(tabs, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("إغلاق")
        close_btn.setObjectName("GhostBtn")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

    def _populate_info(self, student):
        """Fill the info block with all known + custom fields."""
        # Clear existing rows first
        while self.info_layout.count():
            item = self.info_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        muted = theme_manager.muted_text_color()
        text = theme_manager.text_color()

        items: list[tuple[str, str]] = []
        if student.phone:
            items.append(("واتساب الطالب", student.phone))
        parent_phone = getattr(student, "parent_phone", "") or ""
        if parent_phone:
            items.append(("رقم ولي الأمر", parent_phone))
        if student.zoom_link:
            zoom_name = getattr(student, "zoom_link_name", "") or ""
            display = f"{zoom_name} — {student.zoom_link}" if zoom_name else student.zoom_link
            items.append(("رابط Zoom", display))
        for cf in get_custom_fields(student):
            val = cf.get("value", "")
            if val:
                items.append((cf.get("label", ""), val))

        if not items:
            empty = QLabel("— لا توجد بيانات اتصال إضافية —")
            empty.setStyleSheet(f"color: {muted}; font-style: italic;")
            self.info_layout.addWidget(empty, 0, 0)
            return

        # 2-column grid
        for idx, (lbl, val) in enumerate(items):
            row = idx // 2
            col_base = (idx % 2) * 2
            lbl_w = QLabel(f"{lbl}:")
            lbl_w.setStyleSheet(f"color: {muted}; font-size: 12px;")
            val_w = QLabel(val)
            val_w.setStyleSheet(f"color: {text}; font-size: 13px; font-weight: 600;")
            val_w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val_w.setWordWrap(True)
            self.info_layout.addWidget(lbl_w, row, col_base)
            self.info_layout.addWidget(val_w, row, col_base + 1)

    def _build_add_session_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        self.session_date = QDateTimeEdit(datetime.now())
        self.session_date.setCalendarPopup(True)
        self.session_date.setDisplayFormat("yyyy-MM-dd HH:mm")

        self.session_free = QCheckBox("حصة مجانية (تعويضية/تجريبية) — لا تُحسب في الدورة")

        self.lesson_summary = QPlainTextEdit()
        self.lesson_summary.setPlaceholderText("ما الذي تم شرحه في الحصة؟ (يظهر في السجل ويساعدك لاحقاً)")
        self.lesson_summary.setFixedHeight(80)

        self.session_notes = QLineEdit()
        self.session_notes.setPlaceholderText("ملاحظات قصيرة (اختياري)")

        form.addRow("التاريخ والوقت:", self.session_date)
        form.addRow("", self.session_free)
        form.addRow("ما تم شرحه:", self.lesson_summary)
        form.addRow("ملاحظات:", self.session_notes)
        layout.addLayout(form)

        add_btn = QPushButton("إضافة الحصة")
        add_btn.setObjectName("SuccessBtn")
        add_btn.clicked.connect(self._add_session_manually)
        layout.addWidget(add_btn, alignment=Qt.AlignLeft)

        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: #E2E8F0;")
        layout.addSpacing(6)
        layout.addWidget(divider)
        layout.addSpacing(6)

        section = QLabel("تسجيل فيديو مُرسَل للطالب")
        section.setObjectName("SectionTitle")
        layout.addWidget(section)

        vform = QFormLayout()
        self.video_desc = QLineEdit()
        self.video_desc.setPlaceholderText("اختياري — سيُسمَّى تلقائياً «الحصة N» إن تُرك فارغاً")
        vform.addRow("الوصف:", self.video_desc)
        layout.addLayout(vform)

        add_vid = QPushButton("تسجيل فيديو")
        add_vid.clicked.connect(self._add_video)
        layout.addWidget(add_vid, alignment=Qt.AlignLeft)

        layout.addStretch(1)
        return w

    def _build_sessions_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        self.sessions_table = QTableWidget(0, 5)
        self.sessions_table.setHorizontalHeaderLabels(
            ["التاريخ والوقت", "النوع", "الحالة", "ما تم شرحه", "ملاحظات"]
        )
        self.sessions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sessions_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.sessions_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.sessions_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.sessions_table.setAlternatingRowColors(True)
        self.sessions_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sessions_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.sessions_table)

        row_btns = QHBoxLayout()
        toggle_free_btn = QPushButton("تبديل حالة المجانية")
        toggle_free_btn.setObjectName("GhostBtn")
        toggle_free_btn.clicked.connect(self._toggle_free)
        row_btns.addWidget(toggle_free_btn)

        del_btn = QPushButton("حذف المحدد")
        del_btn.setObjectName("DangerBtn")
        del_btn.clicked.connect(self._delete_selected_session)
        row_btns.addWidget(del_btn)

        row_btns.addStretch(1)
        layout.addLayout(row_btns)
        return w

    def _build_videos_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        self.videos_table = QTableWidget(0, 3)
        self.videos_table.setHorizontalHeaderLabels(
            ["التاريخ", "الوصف", "الحالة"]
        )
        self.videos_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.videos_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.videos_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.videos_table.setAlternatingRowColors(True)
        self.videos_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.videos_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.videos_table)

        btn = QPushButton("حذف الفيديو المحدد")
        btn.setObjectName("DangerBtn")
        btn.clicked.connect(self._delete_selected_video)
        layout.addWidget(btn, alignment=Qt.AlignLeft)
        return w

    def refresh(self):
        student = get_student(self.student_id)
        if not student:
            return
        count = counted_sessions(student.id)
        vids = counted_videos(student.id)
        self.summary.setText(
            f"الدورة الحالية: <b style='color:#6366F1'>{count} / {student.sessions_per_cycle}</b> حصص "
            f"&nbsp;•&nbsp; فيديوهات: <b>{vids}</b> "
            f"&nbsp;•&nbsp; سعر الحصة: <b>{student.price_per_session:.2f} {CURRENCY}</b>"
        )
        # Rebuild info block (custom fields may have changed elsewhere)
        if hasattr(self, "info_layout"):
            self._populate_info(student)

        sessions = list_sessions(self.student_id)
        self.sessions_table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            date_item = QTableWidgetItem(s.session_date.strftime("%Y-%m-%d %H:%M"))
            date_item.setData(Qt.UserRole, s.id)
            self.sessions_table.setItem(row, 0, date_item)
            type_txt = "مجانية" if s.is_free else "مدفوعة"
            self.sessions_table.setItem(row, 1, QTableWidgetItem(type_txt))
            status = "الدورة الحالية" if s.counted else "مؤرشفة"
            self.sessions_table.setItem(row, 2, QTableWidgetItem(status))
            self.sessions_table.setItem(row, 3, QTableWidgetItem(s.lesson_summary or ""))
            self.sessions_table.setItem(row, 4, QTableWidgetItem(s.notes or ""))

        videos = list_videos(self.student_id)
        self.videos_table.setRowCount(len(videos))
        for row, v in enumerate(videos):
            item = QTableWidgetItem(v.sent_date.strftime("%Y-%m-%d %H:%M"))
            item.setData(Qt.UserRole, v.id)
            self.videos_table.setItem(row, 0, item)
            self.videos_table.setItem(row, 1, QTableWidgetItem(v.description or ""))
            status = "الدورة الحالية" if v.counted else "مؤرشفة"
            self.videos_table.setItem(row, 2, QTableWidgetItem(status))

    def _add_session_manually(self):
        dt = self.session_date.dateTime().toPython()
        notes = self.session_notes.text().strip()
        summary = self.lesson_summary.toPlainText().strip()
        is_free = self.session_free.isChecked()
        add_session(
            self.student_id, when=dt, notes=notes,
            lesson_summary=summary, is_free=is_free
        )
        self.session_notes.clear()
        self.lesson_summary.clear()
        self.session_free.setChecked(False)
        self.refresh()
        self.data_changed.emit()

    def _add_video(self):
        desc = self.video_desc.text().strip()
        if not desc:
            # Auto-number within the current cycle: الحصة 1 .. الحصة N (=sessions_per_cycle)
            next_num = counted_videos(self.student_id) + 1
            desc = f"الحصة {next_num}"
        add_video(self.student_id, description=desc)
        self.video_desc.clear()
        self.refresh()
        self.data_changed.emit()

    def _delete_selected_session(self):
        row = self.sessions_table.currentRow()
        if row < 0:
            return
        sid = self.sessions_table.item(row, 0).data(Qt.UserRole)
        if QMessageBox.question(self, "حذف", "هل أنت متأكد من حذف هذه الحصة؟") == QMessageBox.Yes:
            delete_session(sid)
            self.refresh()
            self.data_changed.emit()

    def _toggle_free(self):
        row = self.sessions_table.currentRow()
        if row < 0:
            return
        sid = self.sessions_table.item(row, 0).data(Qt.UserRole)
        sessions = list_sessions(self.student_id)
        target = next((s for s in sessions if s.id == sid), None)
        if target:
            update_session(sid, is_free=not target.is_free)
            self.refresh()
            self.data_changed.emit()

    def _delete_selected_video(self):
        row = self.videos_table.currentRow()
        if row < 0:
            return
        vid = self.videos_table.item(row, 0).data(Qt.UserRole)
        if QMessageBox.question(self, "حذف", "هل أنت متأكد من حذف هذا الفيديو؟") == QMessageBox.Yes:
            delete_video(vid)
            self.refresh()
            self.data_changed.emit()

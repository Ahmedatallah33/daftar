from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QGridLayout, QLineEdit, QComboBox, QPushButton
)

from app.services.student_service import (
    counted_sessions_map,
    counted_videos_map,
    list_students,
)
from app.ui.widgets.student_card import StudentCard
from app.ui.widgets.student_detail import StudentDetailDialog


# Filter modes
FILTER_ALL = "all"
FILTER_DUE = "due"            # count >= cycle
FILTER_ACTIVE = "active"      # has remaining sessions in cycle
FILTER_NO_PHONE = "no_phone"  # missing student phone
FILTER_NO_PARENT = "no_parent"  # missing parent_phone
FILTER_HAS_GROUP = "has_group"  # has whatsapp_group_link

_FILTERS_AR = [
    (FILTER_ALL, "كل الطلاب"),
    (FILTER_DUE, "اكتملت الباقة (مستحق)"),
    (FILTER_ACTIVE, "باقة نشطة"),
    (FILTER_NO_PHONE, "بدون رقم واتساب"),
    (FILTER_NO_PARENT, "بدون رقم ولي أمر"),
    (FILTER_HAS_GROUP, "لديه مجموعة واتساب"),
]


class StudentsPage(QWidget):
    data_changed = Signal()

    def __init__(self, auto_refresh: bool = True):
        super().__init__()
        self.setObjectName("CentralSurface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(6)

        # ---- Title row + search + filter + reset ----
        title_row = QHBoxLayout()
        title = QLabel("سجل الطلاب")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)

        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText("🔍  بحث بالاسم/الهاتف/ولي الأمر/الملاحظات...")
        self.search.setMinimumWidth(320)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        title_row.addWidget(self.search)

        self.filter_combo = QComboBox()
        self.filter_combo.setMinimumWidth(180)
        for code, label in _FILTERS_AR:
            self.filter_combo.addItem(label, code)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        title_row.addWidget(self.filter_combo)

        self.reset_btn = QPushButton("إعادة الضبط")
        self.reset_btn.setObjectName("GhostBtn")
        self.reset_btn.setToolTip("مسح البحث وإعادة التصفية")
        self.reset_btn.clicked.connect(self._reset_filters)
        title_row.addWidget(self.reset_btn)

        layout.addLayout(title_row)

        # ---- Subtitle + match count ----
        sub_row = QHBoxLayout()
        self.subtitle = QLabel("البطاقات البرتقالية/الحمراء تعني أن الطالب مستحق للدفع.")
        self.subtitle.setObjectName("PageSubtitle")
        sub_row.addWidget(self.subtitle)
        sub_row.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        sub_row.addWidget(self.count_label)
        layout.addLayout(sub_row)

        layout.addSpacing(14)

        # ---- Scroll area ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }")
        layout.addWidget(self.scroll, 1)

        self.container = QWidget()
        v = QVBoxLayout(self.container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Grid for cards
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(18)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignRight)
        v.addWidget(self.grid_host)

        # Empty state label (shown when filter yields nothing)
        self.empty_label = QLabel("لا يوجد طلاب يطابقون البحث/التصفية الحالية.")
        self.empty_label.setObjectName("PageSubtitle")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(
            "color: #6B7280; font-size: 14px; padding: 40px;"
        )
        self.empty_label.setVisible(False)
        v.addWidget(self.empty_label)

        v.addStretch(1)
        self.scroll.setWidget(self.container)

        # Parallel data: list of dicts {card, student, count, cycle}
        self.card_data: list[dict] = []
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(80)
        self._relayout_timer.timeout.connect(self._relayout)
        if auto_refresh:
            self.refresh()

    # ----------------------------------------------------------------- data
    def refresh(self):
        # Clear old cards
        for d in self.card_data:
            d["card"].deleteLater()
        self.card_data.clear()

        students = list_students(active_only=True)
        session_counts = counted_sessions_map()
        video_counts = counted_videos_map()
        for s in students:
            count = session_counts.get(s.id, 0)
            card = StudentCard(s, count=count, videos=video_counts.get(s.id, 0))
            card.clicked.connect(self._open_detail)
            cycle = int(s.sessions_per_cycle or 8)
            self.card_data.append({
                "card": card,
                "student": s,
                "count": count,
                "cycle": cycle,
            })
        self._apply_filter()

    # -------------------------------------------------------------- filter
    def _matches_search(self, student, q: str) -> bool:
        if not q:
            return True
        q = q.lower()
        haystacks = [
            (student.name or "").lower(),
            (student.phone or "").lower(),
            (getattr(student, "parent_phone", "") or "").lower(),
            (getattr(student, "zoom_link_name", "") or "").lower(),
            (student.notes or "").lower(),
        ]
        return any(q in h for h in haystacks)

    def _matches_filter(self, data: dict, mode: str) -> bool:
        student = data["student"]
        count = data["count"]
        cycle = data["cycle"]

        if mode == FILTER_ALL:
            return True
        if mode == FILTER_DUE:
            return count >= cycle
        if mode == FILTER_ACTIVE:
            return count < cycle
        if mode == FILTER_NO_PHONE:
            return not (student.phone or "").strip()
        if mode == FILTER_NO_PARENT:
            return not (getattr(student, "parent_phone", "") or "").strip()
        if mode == FILTER_HAS_GROUP:
            return bool((getattr(student, "whatsapp_group_link", "") or "").strip())
        return True

    def _apply_filter(self):
        q = self.search.text().strip()
        mode = self.filter_combo.currentData() or FILTER_ALL

        visible_count = 0
        for d in self.card_data:
            ok_search = self._matches_search(d["student"], q)
            ok_filter = self._matches_filter(d, mode)
            visible = ok_search and ok_filter
            d["card"].setVisible(visible)
            if visible:
                visible_count += 1

        total = len(self.card_data)
        if visible_count == total:
            self.count_label.setText(f"{total} طالب")
        else:
            self.count_label.setText(f"{visible_count} من {total} طالب")

        # Toggle empty state
        self.empty_label.setVisible(visible_count == 0 and total > 0)
        self.grid_host.setVisible(visible_count > 0)
        self._relayout()

    def _reset_filters(self):
        self.search.blockSignals(True)
        self.filter_combo.blockSignals(True)
        self.search.clear()
        self.filter_combo.setCurrentIndex(0)
        self.search.blockSignals(False)
        self.filter_combo.blockSignals(False)
        self._apply_filter()

    # ------------------------------------------------------------ layout
    def _relayout(self):
        while self.grid.count():
            self.grid.takeAt(0)
        width = max(self.width(), 800)
        cols = max(1, (width - 60) // 310)
        cols = min(cols, 6)
        row = col = 0
        for d in self.card_data:
            c = d["card"]
            if c.isHidden():
                continue
            self.grid.addWidget(c, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_timer.start()

    # ------------------------------------------------------------ details
    def _open_detail(self, student_id: int):
        dlg = StudentDetailDialog(student_id, self)
        dlg.data_changed.connect(self._on_detail_change)
        dlg.exec()

    def _on_detail_change(self):
        self.data_changed.emit()

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar
)

from app.config import WEEKDAYS, WEEKDAY_AR
from app.db.models import Student
from app.services.student_service import (
    counted_sessions, get_day_schedules, counted_videos
)
from app.ui.helpers.shadow import add_shadow
from app.ui.helpers.time_format import format_time_ar


class StudentCard(QFrame):
    clicked = Signal(int)

    def __init__(
        self,
        student: Student,
        parent=None,
        *,
        count: int | None = None,
        videos: int | None = None,
    ):
        super().__init__(parent)
        self.student_id = student.id
        self.student_name = student.name
        self.setObjectName("Card")
        self.setFixedSize(290, 220)
        self.setCursor(Qt.PointingHandCursor)

        add_shadow(self, blur=20, y_offset=4, opacity=24)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(student.name)
        name.setObjectName("StudentName")
        name.setWordWrap(True)
        top.addWidget(name, 1)

        if count is None:
            count = counted_sessions(student.id)
        cycle = student.sessions_per_cycle or 8
        is_due = count >= cycle

        if is_due:
            badge = QLabel("مستحق")
            badge.setObjectName("BadgeDue")
            top.addWidget(badge, 0, Qt.AlignTop)

        layout.addLayout(top)

        counter_row = QHBoxLayout()
        counter = QLabel(str(count))
        counter.setObjectName("CounterBigDue" if is_due else "CounterBig")
        counter.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        limit = QLabel(f"من {cycle} حصص")
        limit.setObjectName("CounterSmall")
        limit.setAlignment(Qt.AlignLeft | Qt.AlignBottom)

        counter_row.addWidget(counter, 0, Qt.AlignBottom)
        counter_row.addWidget(limit, 1, Qt.AlignBottom)
        layout.addLayout(counter_row)

        bar = QProgressBar()
        bar.setRange(0, cycle)
        bar.setValue(min(count, cycle))
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        if is_due:
            bar.setProperty("status", "overdue" if count > cycle else "due")
        layout.addWidget(bar)

        # Per-day schedule summary: build compact text
        # Now day_sched[c] is a list of times
        day_sched = get_day_schedules(student)
        ordered = [(c, day_sched[c]) for c, _ in WEEKDAYS if c in day_sched]

        if ordered:
            # Collect all unique times across all days
            all_times = set()
            for _, times_list in ordered:
                all_times.update(times_list)

            if len(all_times) == 1 and len(ordered) > 1:
                # All times identical: show "Days  🕐  shared-time"
                days_text = "، ".join(WEEKDAY_AR.get(c, c) for c, _ in ordered)
                shared_time = next(iter(all_times))
                meta_row = QHBoxLayout()
                meta_row.setSpacing(12)
                days_label = QLabel(f"🗓  {days_text}")
                days_label.setObjectName("StudentMeta")
                meta_row.addWidget(days_label)
                time_label = QLabel(f"🕐  {format_time_ar(shared_time)}")
                time_label.setObjectName("StudentMeta")
                meta_row.addWidget(time_label)
                meta_row.addStretch(1)
                layout.addLayout(meta_row)
            else:
                # Mixed: show each day with its times (separated by ، for multiple times per day)
                parts = []
                for c, times_list in ordered:
                    times_ar = " ، ".join(format_time_ar(t) for t in times_list)
                    parts.append(f"{WEEKDAY_AR.get(c, c)}: {times_ar}")
                sched_text = "  •  ".join(parts)
                sched_label = QLabel(f"🗓  {sched_text}")
                sched_label.setObjectName("StudentMeta")
                sched_label.setWordWrap(True)
                sched_label.setToolTip(sched_text)
                layout.addWidget(sched_label)
        else:
            empty_label = QLabel("🗓  لم تُحدد أيام بعد")
            empty_label.setObjectName("StudentMeta")
            layout.addWidget(empty_label)

        if videos is None:
            videos = counted_videos(student.id)
        price = (student.price_per_session or 0) * count
        foot = QLabel(
            f"<span style='color:#64748B'>فيديوهات: </span><b>{videos}</b>"
            f"  •  <span style='color:#64748B'>المبلغ: </span><b>{price:.0f}</b>"
        )
        foot.setObjectName("StudentMeta")
        layout.addWidget(foot)

        if is_due:
            self.setProperty("status", "overdue" if count > cycle else "due")
            self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.student_id)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        add_shadow(self, blur=30, y_offset=8, opacity=45)
        super().enterEvent(event)

    def leaveEvent(self, event):
        add_shadow(self, blur=20, y_offset=4, opacity=24)
        super().leaveEvent(event)

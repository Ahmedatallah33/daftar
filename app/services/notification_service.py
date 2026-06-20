from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from app.services.student_service import list_students, get_day_schedules
from app.services.settings_service import get_notifications_enabled, get_notification_minutes

PYTHON_WEEKDAY_MAP = {
    0: "MON", 1: "TUE", 2: "WED", 3: "THU", 4: "FRI", 5: "SAT", 6: "SUN"
}


class NotificationService(QObject):
    upcoming_lesson = Signal(str, str, int)  # student_name, session_time, minutes_until

    def __init__(self, parent=None):
        super().__init__(parent)
        self._notified_today = set()
        self._current_day = None
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self._check)

    def start(self):
        self.timer.start()
        self._check()

    def stop(self):
        self.timer.stop()

    def _check(self):
        if not get_notifications_enabled():
            return
        now = datetime.now()
        today_code = PYTHON_WEEKDAY_MAP[now.weekday()]

        if self._current_day != now.date():
            self._current_day = now.date()
            self._notified_today.clear()

        minutes_ahead = get_notification_minutes()
        window_start = now
        window_end = now + timedelta(minutes=minutes_ahead)

        for s in list_students(active_only=True):
            day_sched = get_day_schedules(s)
            today_times = day_sched.get(today_code) or []
            if not today_times:
                continue

            for today_time in today_times:
                # Use composite key to avoid duplicate notifications for same student + time
                notif_key = (s.id, today_code, today_time)
                if notif_key in self._notified_today:
                    continue

                try:
                    h, m = today_time.split(":")
                    lesson_dt = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
                except (ValueError, AttributeError):
                    continue

                if window_start <= lesson_dt <= window_end:
                    diff_minutes = int((lesson_dt - now).total_seconds() // 60)
                    self._notified_today.add(notif_key)
                    self.upcoming_lesson.emit(s.name, today_time, diff_minutes)

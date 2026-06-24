import json
from typing import Dict, List, Optional
from sqlalchemy import func

from app.db.engine import get_session, session_scope
from app.db.models import Student, Session as SessionModel, Video


def list_students(active_only: bool = True) -> List[Student]:
    s = get_session()
    q = s.query(Student)
    if active_only:
        q = q.filter(Student.is_active == True)  # noqa: E712
    return q.order_by(Student.name).all()


def get_student(student_id: int) -> Optional[Student]:
    return get_session().get(Student, student_id)


def create_student(
    name: str,
    phone: str = "",
    zoom_link: str = "",
    price_per_session: float = 0.0,
    sessions_per_cycle: int = 8,
    weekly_schedule: Optional[List[str]] = None,
    session_time: str = "",
    notes: str = "",
    day_schedules: Optional[Dict[str, List[str]]] = None,
    parent_phone: str = "",
    zoom_link_name: str = "",
    whatsapp_group_link: str = "",
    custom_fields: Optional[List[Dict]] = None,
) -> Student:
    name = (name or "").strip()
    if not name:
        raise ValueError("اسم الطالب مطلوب")
    price_per_session = float(price_per_session or 0)
    if price_per_session < 0:
        raise ValueError("سعر الحصة لا يمكن أن يكون سالباً")
    sessions_per_cycle = int(sessions_per_cycle or 8)
    if sessions_per_cycle < 1:
        raise ValueError("عدد الحصص في الدورة يجب أن يكون 1 على الأقل")

    # Derive weekly_schedule + session_time from day_schedules if given
    if day_schedules:
        weekly_schedule = list(day_schedules.keys())
        if not session_time:
            # Use first day's first time as legacy fallback
            first_list = next(iter(day_schedules.values()), [])
            session_time = first_list[0] if first_list else ""

    student = Student(
        name=name,
        phone=phone.strip(),
        zoom_link=zoom_link.strip(),
        price_per_session=price_per_session,
        sessions_per_cycle=sessions_per_cycle,
        weekly_schedule=json.dumps(weekly_schedule or []),
        session_time=session_time.strip(),
        day_schedules=json.dumps(_normalize_schedules(day_schedules or {}), ensure_ascii=False),
        parent_phone=(parent_phone or "").strip(),
        zoom_link_name=(zoom_link_name or "").strip(),
        whatsapp_group_link=(whatsapp_group_link or "").strip(),
        custom_fields=json.dumps(
            _sanitize_custom_fields(custom_fields or []), ensure_ascii=False
        ),
        notes=notes.strip(),
        is_active=True,
    )
    with session_scope() as s:
        s.add(student)
        s.flush()
    return student


def _normalize_schedules(schedules: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Deduplicate and sort times for each day."""
    result = {}
    for day, times in schedules.items():
        if isinstance(times, list):
            unique_sorted = sorted(set(str(t).strip() for t in times if str(t).strip()))
            if unique_sorted:
                result[day] = unique_sorted
    return result


def _sanitize_custom_fields(items) -> List[Dict]:
    """Keep only well-formed entries; coerce types; drop empty labels."""
    clean = []
    if not isinstance(items, list):
        return clean
    for it in items:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label", "")).strip()
        if not label:
            continue
        value = str(it.get("value", "")).strip()
        show = bool(it.get("show_in_popup", False))
        clean.append({"label": label, "value": value, "show_in_popup": show})
    return clean


def update_student(student_id: int, **fields) -> Optional[Student]:
    # Validate incoming values before touching the DB
    if "name" in fields and not (fields["name"] or "").strip():
        raise ValueError("اسم الطالب مطلوب")
    if fields.get("price_per_session") is not None and float(fields["price_per_session"]) < 0:
        raise ValueError("سعر الحصة لا يمكن أن يكون سالباً")
    if fields.get("sessions_per_cycle") is not None and int(fields["sessions_per_cycle"]) < 1:
        raise ValueError("عدد الحصص في الدورة يجب أن يكون 1 على الأقل")

    with session_scope() as s:
        student = s.get(Student, student_id)
        if not student:
            return None

        # Keep the legacy schedule fields synchronized with the structured value.
        if "day_schedules" in fields and isinstance(fields["day_schedules"], dict):
            ds = fields["day_schedules"]
            fields["weekly_schedule"] = list(ds.keys())
            if ds and not fields.get("session_time"):
                first_list = next(iter(ds.values()), [])
                fields["session_time"] = first_list[0] if first_list else ""
            fields["day_schedules"] = json.dumps(
                _normalize_schedules(ds), ensure_ascii=False
            )

        if "weekly_schedule" in fields and isinstance(fields["weekly_schedule"], list):
            fields["weekly_schedule"] = json.dumps(fields["weekly_schedule"])

        if "custom_fields" in fields and isinstance(fields["custom_fields"], list):
            fields["custom_fields"] = json.dumps(
                _sanitize_custom_fields(fields["custom_fields"]), ensure_ascii=False
            )

        for key, val in fields.items():
            if hasattr(student, key):
                setattr(student, key, val)
    return student


def delete_student(student_id: int, soft: bool = True) -> bool:
    with session_scope() as s:
        student = s.get(Student, student_id)
        if not student:
            return False
        if soft:
            student.is_active = False
        else:
            s.delete(student)
    return True


def get_weekly_schedule(student: Student) -> List[str]:
    try:
        return json.loads(student.weekly_schedule or "[]")
    except json.JSONDecodeError:
        return []


def get_custom_fields(student: Student) -> List[Dict]:
    """Returns the student's user-defined fields as a list of dicts.

    Each item: {"label": str, "value": str, "show_in_popup": bool}.
    Always returns a list; silently repairs malformed data.
    """
    raw = getattr(student, "custom_fields", None) or ""
    try:
        data = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []
    return _sanitize_custom_fields(data)


def get_day_schedules(student: Student) -> Dict[str, List[str]]:
    """Returns a {day_code: ['HH:MM', 'HH:MM']} dict (normalized to list format).

    Handles:
      - New format: {"SAT": ["17:00", "20:00"]} -> as-is
      - Old format: {"SAT": "17:00"} -> {"SAT": ["17:00"]}
      - Legacy: weekly_schedule + session_time -> {day: [time]}

    Always returns Dict[str, List[str]] with deduped, sorted times.
    """
    raw = getattr(student, "day_schedules", None) or ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}

    result: Dict[str, List[str]] = {}
    if isinstance(data, dict) and data:
        for k, v in data.items():
            if isinstance(v, list):
                # New format: list of times
                times = [str(t).strip() for t in v if str(t).strip()]
            elif isinstance(v, str) and v.strip():
                # Old format: single time string
                times = [v.strip()]
            else:
                continue
            if times:
                result[str(k)] = sorted(set(times))

    if result:
        return result

    # Legacy fallback: weekly_schedule + session_time
    days = get_weekly_schedule(student)
    legacy_time = (student.session_time or "").strip()
    if not days or not legacy_time:
        return {}
    return {d: [legacy_time] for d in days}


def counted_sessions(student_id: int) -> int:
    """Counts only paid sessions (excludes free/trial sessions)."""
    s = get_session()
    return s.query(func.count(SessionModel.id)).filter(
        SessionModel.student_id == student_id,
        SessionModel.counted == True,  # noqa: E712
        SessionModel.is_free == False,  # noqa: E712
    ).scalar() or 0


def counted_sessions_map() -> dict[int, int]:
    """Return paid-session counts for all students in one aggregate query."""
    s = get_session()
    rows = s.query(
        SessionModel.student_id,
        func.count(SessionModel.id),
    ).filter(
        SessionModel.counted == True,  # noqa: E712
        SessionModel.is_free == False,  # noqa: E712
    ).group_by(SessionModel.student_id).all()
    return {int(student_id): int(count) for student_id, count in rows}


def total_counted_sessions(student_id: int) -> int:
    """Counts all current-cycle sessions including free ones (for history)."""
    s = get_session()
    return s.query(func.count(SessionModel.id)).filter(
        SessionModel.student_id == student_id,
        SessionModel.counted == True,  # noqa: E712
    ).scalar() or 0


def counted_videos(student_id: int) -> int:
    s = get_session()
    return s.query(func.count(Video.id)).filter(
        Video.student_id == student_id,
        Video.counted == True,  # noqa: E712
    ).scalar() or 0


def counted_videos_map() -> dict[int, int]:
    """Return counted-video totals for all students in one aggregate query."""
    s = get_session()
    rows = s.query(
        Video.student_id,
        func.count(Video.id),
    ).filter(
        Video.counted == True,  # noqa: E712
    ).group_by(Video.student_id).all()
    return {int(student_id): int(count) for student_id, count in rows}

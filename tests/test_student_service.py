"""اختبارات خدمة الطلاب: التحقق من المدخلات، تحليل الجداول، الحقول المخصصة."""
import json

import pytest

from app.services import student_service as ss
from app.services.student_service import (
    create_student, update_student, delete_student, get_student,
    get_day_schedules, get_weekly_schedule, get_custom_fields,
    list_students,
)
from app.db.engine import get_session
from app.db.models import Session, Student, Video


# ---- CRUD أساسي ----

def test_create_and_get_student():
    st = create_student(name="معاذ", price_per_session=50, sessions_per_cycle=8)
    assert st.id is not None
    assert get_student(st.id).name == "معاذ"


def test_soft_delete_hides_from_active_list():
    st = create_student(name="معاذ")
    delete_student(st.id, soft=True)
    assert all(s.id != st.id for s in list_students(active_only=True))
    assert any(s.id == st.id for s in list_students(active_only=False))


def test_name_is_trimmed():
    st = create_student(name="  معاذ  ")
    assert st.name == "معاذ"


# ---- التحقق من المدخلات (يُتوقَّع رفضها) ----

def test_create_rejects_empty_name():
    with pytest.raises(ValueError):
        create_student(name="   ")


def test_create_rejects_negative_price():
    with pytest.raises(ValueError):
        create_student(name="معاذ", price_per_session=-5)


def test_create_rejects_non_positive_cycle():
    with pytest.raises(ValueError):
        create_student(name="معاذ", sessions_per_cycle=-3)


def test_update_rejects_non_positive_cycle():
    st = create_student(name="معاذ", sessions_per_cycle=8)
    with pytest.raises(ValueError):
        update_student(st.id, sessions_per_cycle=0)


# ---- الجداول الزمنية ----

def test_day_schedules_new_format_roundtrip():
    st = create_student(name="معاذ", day_schedules={"SAT": ["17:00", "20:00"]})
    sched = get_day_schedules(st)
    assert sched == {"SAT": ["17:00", "20:00"]}


def test_day_schedules_old_string_format_converted():
    st = create_student(name="معاذ")
    # محاكاة الصيغة القديمة: {"SAT": "17:00"}
    s = get_session()
    obj = s.get(Student, st.id)
    obj.day_schedules = json.dumps({"SUN": "18:00"})
    s.commit()
    sched = get_day_schedules(get_student(st.id))
    assert sched == {"SUN": ["18:00"]}


def test_corrupt_day_schedules_returns_empty():
    st = create_student(name="معاذ")
    s = get_session()
    obj = s.get(Student, st.id)
    obj.day_schedules = "{ this is not json"
    s.commit()
    # لا يجب أن ينهار — يعود فارغاً بهدوء
    assert get_day_schedules(get_student(st.id)) == {}


def test_corrupt_weekly_schedule_returns_empty():
    st = create_student(name="معاذ")
    s = get_session()
    obj = s.get(Student, st.id)
    obj.weekly_schedule = "!!broken!!"
    s.commit()
    assert get_weekly_schedule(get_student(st.id)) == []


# ---- الحقول المخصصة ----

def test_custom_fields_sanitized():
    st = create_student(
        name="معاذ",
        custom_fields=[
            {"label": "المدرسة", "value": "النور", "show_in_popup": True},
            {"label": "", "value": "تُحذف"},          # تسمية فارغة → تُسقط
            "not-a-dict",                               # ليست dict → تُسقط
        ],
    )
    fields = get_custom_fields(get_student(st.id))
    assert len(fields) == 1
    assert fields[0]["label"] == "المدرسة"
    assert fields[0]["show_in_popup"] is True


def test_corrupt_custom_fields_returns_empty():
    st = create_student(name="معاذ")
    s = get_session()
    obj = s.get(Student, st.id)
    obj.custom_fields = "<<bad>>"
    s.commit()
    assert get_custom_fields(get_student(st.id)) == []


def test_counted_sessions_map_matches_single_student_counts():
    first = create_student(name="الأول")
    second = create_student(name="الثاني")
    empty = create_student(name="بدون حصص")
    s = get_session()
    s.add_all([
        Session(student_id=first.id, counted=True, is_free=False),
        Session(student_id=first.id, counted=True, is_free=False),
        Session(student_id=first.id, counted=True, is_free=True),
        Session(student_id=first.id, counted=False, is_free=False),
        Session(student_id=second.id, counted=True, is_free=False),
    ])
    s.commit()

    counts = ss.counted_sessions_map()
    for student in (first, second, empty):
        assert counts.get(student.id, 0) == ss.counted_sessions(student.id)


def test_counted_videos_map_matches_single_student_counts():
    first = create_student(name="الأول")
    second = create_student(name="الثاني")
    empty = create_student(name="بدون فيديو")
    s = get_session()
    s.add_all([
        Video(student_id=first.id, counted=True),
        Video(student_id=first.id, counted=True),
        Video(student_id=first.id, counted=False),
        Video(student_id=second.id, counted=True),
    ])
    s.commit()

    counts = ss.counted_videos_map()
    for student in (first, second, empty):
        assert counts.get(student.id, 0) == ss.counted_videos(student.id)

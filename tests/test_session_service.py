"""اختبارات خدمة الحصص والفيديوهات."""
from datetime import datetime

from app.services.student_service import create_student, counted_sessions
from app.services import session_service as sess


def _student():
    return create_student(name="طالب", price_per_session=50, sessions_per_cycle=8)


def test_add_and_list_sessions():
    st = _student()
    sess.add_session(st.id)
    sess.add_session(st.id)
    assert len(sess.list_sessions(st.id)) == 2
    assert counted_sessions(st.id) == 2


def test_explicit_past_date_is_kept():
    """datetime ماضٍ يجب أن يُحفظ كما هو (تأكيد أن `when or now` لا يستبدله)."""
    st = _student()
    past = datetime(1970, 1, 1, 12, 0)
    s = sess.add_session(st.id, when=past)
    assert s.session_date == past


def test_undo_last_session_returns_snapshot_and_deletes():
    st = _student()
    sess.add_session(st.id, notes="أولى")
    sess.add_session(st.id, notes="أخيرة")
    snap = sess.undo_last_session(st.id)
    assert isinstance(snap, dict)
    assert snap["notes"] == "أخيرة"
    assert len(sess.list_sessions(st.id)) == 1


def test_undo_with_no_sessions_returns_none():
    st = _student()
    assert sess.undo_last_session(st.id) is None


def test_update_session_fields():
    st = _student()
    s = sess.add_session(st.id, notes="قديم")
    sess.update_session(s.id, notes="جديد", lesson_summary="ملخص")
    updated = sess.list_sessions(st.id)[0]
    assert updated.notes == "جديد"
    assert updated.lesson_summary == "ملخص"


def test_update_missing_session_returns_none():
    assert sess.update_session(99999, notes="x") is None


def test_delete_session():
    st = _student()
    s = sess.add_session(st.id)
    assert sess.delete_session(s.id) is True
    assert sess.list_sessions(st.id) == []
    assert sess.delete_session(s.id) is False


def test_videos_crud():
    st = _student()
    v = sess.add_video(st.id, "فيديو شرح")
    assert len(sess.list_videos(st.id)) == 1
    assert sess.delete_video(v.id) is True
    assert sess.list_videos(st.id) == []

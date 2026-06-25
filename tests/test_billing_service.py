"""اختبارات منطق الفواتير والمستحقات."""
from datetime import datetime

from app.services.student_service import create_student
from app.services.session_service import add_session, add_video
from app.services import billing_service as billing


def _student(**kw):
    defaults = dict(name="طالب", price_per_session=50.0, sessions_per_cycle=8)
    defaults.update(kw)
    return create_student(**defaults)


# ---- students_with_dues ----

def test_no_dues_below_threshold():
    st = _student(sessions_per_cycle=8)
    for _ in range(7):
        add_session(st.id)
    assert billing.students_with_dues() == []


def test_dues_at_threshold():
    st = _student(price_per_session=50.0, sessions_per_cycle=8)
    for _ in range(8):
        add_session(st.id)
    dues = billing.students_with_dues()
    assert len(dues) == 1
    assert dues[0]["sessions"] == 8
    assert dues[0]["amount"] == 400.0


def test_free_sessions_not_counted_toward_dues():
    st = _student(sessions_per_cycle=8)
    for _ in range(8):
        add_session(st.id, is_free=True)
    # كل الحصص مجانية → لا مستحقات
    assert billing.students_with_dues() == []


def test_dues_amount_float_rounding():
    """3 × 0.1 يجب أن تساوي 0.30 لا 0.30000000000000004 (خطأ float)."""
    st = _student(price_per_session=0.1, sessions_per_cycle=3)
    for _ in range(3):
        add_session(st.id)
    dues = billing.students_with_dues()
    assert len(dues) == 1
    assert dues[0]["amount"] == 0.3


def test_zero_cycle_does_not_flag_single_session():
    """بيانات قديمة/تالفة بدورة = 0 لا يجب أن تجعل أي حصة واحدة مستحقة فوراً."""
    st = _student(sessions_per_cycle=8)
    # محاكاة بيانات تالفة: نضبط الدورة = 0 مباشرة في القاعدة (تجاوز التحقق)
    from app.db.engine import get_session
    from app.db.models import Student
    s = get_session()
    s.get(Student, st.id).sessions_per_cycle = 0
    s.commit()
    add_session(st.id)
    assert billing.students_with_dues() == []


# ---- reset_cycle ----

def test_reset_cycle_clears_counted():
    st = _student(sessions_per_cycle=8)
    for _ in range(8):
        add_session(st.id)
    add_video(st.id, "شرح")
    billing.reset_cycle(st.id)
    assert billing.students_with_dues() == []
    from app.services.student_service import counted_sessions, counted_videos
    assert counted_sessions(st.id) == 0
    assert counted_videos(st.id) == 0


# ---- record / mark / delete invoice ----

def test_record_and_list_invoice():
    st = _student()
    inv = billing.record_invoice(st.id, 8, 0, 400.0, "x.pdf")
    assert inv.id is not None
    assert len(billing.list_invoices()) == 1
    assert len(billing.list_invoices(only_paid=False)) == 1
    assert len(billing.list_invoices(only_paid=True)) == 0


def test_mark_invoice_paid_toggles_state():
    st = _student()
    inv = billing.record_invoice(st.id, 8, 0, 400.0, "x.pdf")
    billing.mark_invoice_paid(inv.id, True)
    assert len(billing.list_invoices(only_paid=True)) == 1
    paid = billing.list_invoices(only_paid=True)[0]
    assert paid.paid_at is not None
    billing.mark_invoice_paid(inv.id, False)
    assert paid.paid_at is None


def test_mark_missing_invoice_returns_none():
    assert billing.mark_invoice_paid(99999, True) is None


def test_delete_invoice():
    st = _student()
    inv = billing.record_invoice(st.id, 8, 0, 400.0, "x.pdf")
    assert billing.delete_invoice(inv.id) is True
    assert billing.list_invoices() == []
    assert billing.delete_invoice(inv.id) is False


# ---- reset_all_activity ----

def test_reset_all_activity_keeps_students():
    st = _student()
    add_session(st.id)
    billing.record_invoice(st.id, 1, 0, 50.0, "x.pdf")
    backup_path, counts = billing.reset_all_activity(keep_students=True)
    assert backup_path.exists()
    assert counts["sessions"] == 1
    assert counts["invoices"] == 1
    assert counts["students"] == 0
    from app.services.student_service import list_students
    assert len(list_students()) == 1  # الطالب باقٍ


def test_reset_all_activity_full_wipe():
    st = _student()
    add_session(st.id)
    backup_path, counts = billing.reset_all_activity(keep_students=False)
    assert backup_path.exists()
    assert counts["students"] == 1
    from app.services.student_service import list_students
    assert list_students() == []


# ---- monthly_stats ----

def test_monthly_stats_sums_income_in_month():
    st = _student()
    inv = billing.record_invoice(st.id, 8, 0, 400.0, "x.pdf", is_paid=True)
    inv.issued_at = datetime(2026, 6, 15, 10, 0)
    from app.db.engine import get_session
    get_session().commit()
    stats = billing.monthly_stats(2026, 6)
    assert stats["income"] == 400.0
    assert stats["paid_income"] == 400.0
    assert stats["pending_income"] == 0.0


def test_monthly_stats_empty_month():
    stats = billing.monthly_stats(2099, 1)
    assert stats["income"] == 0.0
    assert stats["sessions"] == 0
    assert stats["top_students"] == []

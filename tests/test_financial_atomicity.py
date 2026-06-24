import pytest

from app.db.engine import SessionLocal, get_session
from app.db.models import Invoice
from app.services import billing_service as billing
from app.services.session_service import add_session, add_video
from app.services.student_service import counted_sessions, counted_videos, create_student


def _due_student():
    student = create_student(
        "طالب مالي", price_per_session=50, sessions_per_cycle=2
    )
    add_session(student.id)
    add_session(student.id)
    add_video(student.id, "شرح")
    return student


def _invoice_for_due(student):
    due = billing.students_with_dues()[0]
    return billing.record_invoice(
        student.id,
        due["sessions"],
        due["videos"],
        due["amount"],
        "invoice.pdf",
    )


def _legacy_invoice_for_due(student):
    invoice = _invoice_for_due(student)
    session = get_session()
    session.get(Invoice, invoice.id).cycle_signature = ""
    session.commit()
    SessionLocal.remove()
    return invoice.id


def test_invoice_is_issued_unpaid_and_reset_is_guarded():
    student = _due_student()
    invoice = _invoice_for_due(student)
    student_id = student.id

    assert invoice.is_paid is False
    assert invoice.paid_at is None
    with pytest.raises(billing.UnpaidInvoiceError):
        billing.reset_cycle(student_id)
    assert counted_sessions(student_id) == 2


def test_duplicate_unpaid_invoice_for_same_cycle_is_rejected():
    student = _due_student()
    _invoice_for_due(student)

    with pytest.raises(billing.DuplicateInvoiceError):
        _invoice_for_due(student)


def test_collect_and_reset_is_one_successful_transaction():
    student = _due_student()
    invoice = _invoice_for_due(student)

    billing.collect_payment_and_reset(invoice.id, student.id)

    SessionLocal.remove()
    stored = get_session().get(Invoice, invoice.id)
    assert stored.is_paid is True
    assert stored.paid_at is not None
    assert counted_sessions(student.id) == 0
    assert counted_videos(student.id) == 0

    with pytest.raises(ValueError, match="تم تحصيل هذه الفاتورة من قبل"):
        billing.collect_payment_and_reset(invoice.id, student.id)


def test_collect_failure_rolls_back_payment_and_reset(monkeypatch):
    student = _due_student()
    invoice = _invoice_for_due(student)
    student_id = student.id
    invoice_id = invoice.id
    real_reset = billing._reset_cycle_in_session

    def fail_reset(session, target_student_id):
        real_reset(session, target_student_id)
        raise RuntimeError("simulated reset failure")

    monkeypatch.setattr(billing, "_reset_cycle_in_session", fail_reset)
    with pytest.raises(RuntimeError, match="simulated reset failure"):
        billing.collect_payment_and_reset(invoice_id, student_id)

    stored = get_session().get(Invoice, invoice_id)
    assert stored.is_paid is False
    assert stored.paid_at is None
    assert counted_sessions(student_id) == 2
    assert counted_videos(student_id) == 1


def test_legacy_unpaid_invoice_blocks_generic_reset():
    student = _due_student()
    invoice_id = _legacy_invoice_for_due(student)

    due = billing.students_with_dues()[0]
    assert due["invoice_id"] is None
    assert due["legacy_invoice_ids"] == [invoice_id]
    with pytest.raises(billing.LegacyInvoiceReconciliationRequired):
        billing.reset_cycle(student.id)
    assert counted_sessions(student.id) == 2
    assert counted_videos(student.id) == 1


def test_legacy_mark_paid_only_preserves_all_activity():
    student = _due_student()
    invoice_id = _legacy_invoice_for_due(student)
    other_student = create_student(
        "طالب آخر", price_per_session=30, sessions_per_cycle=1
    )
    add_session(other_student.id)
    other_invoice = _invoice_for_due(other_student)

    billing.mark_legacy_invoice_paid_only(invoice_id)

    SessionLocal.remove()
    stored = get_session().get(Invoice, invoice_id)
    assert stored.is_paid is True
    assert stored.paid_at is not None
    assert stored.cycle_signature == ""
    assert get_session().get(Invoice, other_invoice.id).is_paid is False
    assert counted_sessions(student.id) == 2
    assert counted_videos(student.id) == 1


def test_matching_legacy_reconciliation_pays_links_and_resets():
    student = _due_student()
    invoice_id = _legacy_invoice_for_due(student)

    billing.reconcile_legacy_invoice_and_reset(invoice_id, student.id)

    SessionLocal.remove()
    stored = get_session().get(Invoice, invoice_id)
    assert stored.is_paid is True
    assert stored.paid_at is not None
    assert stored.cycle_signature.startswith("sessions:")
    assert counted_sessions(student.id) == 0
    assert counted_videos(student.id) == 0


def test_mismatched_legacy_reconciliation_blocks_reset():
    student = _due_student()
    invoice_id = _legacy_invoice_for_due(student)
    add_session(student.id)

    with pytest.raises(billing.LegacyInvoiceCountMismatchError):
        billing.reconcile_legacy_invoice_and_reset(invoice_id, student.id)

    SessionLocal.remove()
    stored = get_session().get(Invoice, invoice_id)
    assert stored.is_paid is False
    assert stored.cycle_signature == ""
    assert counted_sessions(student.id) == 3
    assert counted_videos(student.id) == 1


def test_legacy_reconciliation_failure_rolls_back_link_payment_and_reset(
    monkeypatch,
):
    student = _due_student()
    student_id = student.id
    invoice_id = _legacy_invoice_for_due(student)
    real_reset = billing._reset_cycle_in_session

    def fail_after_reset(session, target_student_id):
        real_reset(session, target_student_id)
        raise RuntimeError("simulated legacy reconciliation failure")

    monkeypatch.setattr(billing, "_reset_cycle_in_session", fail_after_reset)
    with pytest.raises(RuntimeError, match="simulated legacy reconciliation failure"):
        billing.reconcile_legacy_invoice_and_reset(invoice_id, student_id)

    stored = get_session().get(Invoice, invoice_id)
    assert stored.is_paid is False
    assert stored.paid_at is None
    assert stored.cycle_signature == ""
    assert counted_sessions(student_id) == 2
    assert counted_videos(student_id) == 1


def test_collect_rejects_cycle_changed_after_invoice():
    student = _due_student()
    invoice = _invoice_for_due(student)
    add_session(student.id)
    student_id = student.id
    invoice_id = invoice.id

    with pytest.raises(ValueError, match="لا تطابق الدورة الحالية"):
        billing.collect_payment_and_reset(invoice_id, student_id)

    stored = get_session().get(Invoice, invoice_id)
    assert stored.is_paid is False
    assert counted_sessions(student_id) == 3


def test_due_refresh_restores_persisted_invoice_link(qtbot):
    from app.ui.pages.billing_page import BillingPage, DueCard

    student = _due_student()
    invoice = _invoice_for_due(student)
    SessionLocal.remove()

    first = billing.students_with_dues()[0]
    second = billing.students_with_dues()[0]
    assert first["invoice_id"] == invoice.id
    assert second["invoice_id"] == invoice.id

    page = BillingPage(auto_refresh=False)
    qtbot.addWidget(page)
    page.refresh()
    cards = page.findChildren(DueCard)
    assert len(cards) == 1
    assert cards[0].invoice_id == invoice.id
    assert cards[0].pdf_btn.isEnabled() is False


def test_due_card_exposes_named_legacy_reconciliation(qtbot):
    from app.ui.pages.billing_page import BillingPage, DueCard

    student = _due_student()
    invoice_id = _legacy_invoice_for_due(student)
    page = BillingPage(auto_refresh=False)
    qtbot.addWidget(page)
    page.refresh()

    cards = page.findChildren(DueCard)
    assert len(cards) == 1
    assert cards[0].legacy_invoice_ids == [invoice_id]
    assert cards[0].reset_btn.text().strip() == "تسوية فاتورة قديمة"
    assert cards[0].pdf_btn.isEnabled() is False


def test_legacy_dialog_disables_reset_when_counts_mismatch(qtbot):
    from PySide6.QtWidgets import QLabel, QPushButton

    from app.ui.pages.billing_page import LegacyInvoiceReconciliationDialog

    student = _due_student()
    invoice_id = _legacy_invoice_for_due(student)
    add_session(student.id)
    dialog = LegacyInvoiceReconciliationDialog(invoice_id, parent_page=None)
    qtbot.addWidget(dialog)

    link_button = next(
        button
        for button in dialog.findChildren(QPushButton)
        if "ربط بالدورة الحالية" in button.text()
    )
    text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert link_button.isEnabled() is False
    assert "قبل إضافة الربط الدقيق" in text
    assert "الأعداد غير متطابقة" in text

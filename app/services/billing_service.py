from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, or_

from app import config
from app.db.engine import get_session, session_scope
from app.db.models import Invoice, Session as SessionModel, Student, Video
from app.db.safety import online_backup
from app.services.student_service import counted_sessions_map, counted_videos_map


class UnpaidInvoiceError(RuntimeError):
    """Raised when activity would be reset while an invoice remains unpaid."""


class DuplicateInvoiceError(RuntimeError):
    """Raised when the same current cycle already has an unpaid invoice."""


class LegacyInvoiceReconciliationRequired(RuntimeError):
    """Raised when an unpaid pre-signature invoice needs explicit handling."""


class LegacyInvoiceCountMismatchError(RuntimeError):
    """Raised when a legacy invoice cannot be linked to current activity."""


def _is_legacy_unlinked(invoice: Invoice) -> bool:
    return not (invoice.cycle_signature or "").strip()


def _legacy_unlinked_filter(student_id: int):
    return (
        Invoice.student_id == student_id,
        Invoice.is_paid == False,  # noqa: E712
        or_(Invoice.cycle_signature.is_(None), func.trim(Invoice.cycle_signature) == ""),
    )


def _cycle_ids_maps(s) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    sessions: dict[int, list[int]] = {}
    videos: dict[int, list[int]] = {}
    session_rows = s.query(SessionModel.student_id, SessionModel.id).filter(
        SessionModel.counted == True  # noqa: E712
    ).order_by(SessionModel.student_id, SessionModel.id).all()
    video_rows = s.query(Video.student_id, Video.id).filter(
        Video.counted == True  # noqa: E712
    ).order_by(Video.student_id, Video.id).all()
    for student_id, record_id in session_rows:
        sessions.setdefault(student_id, []).append(record_id)
    for student_id, record_id in video_rows:
        videos.setdefault(student_id, []).append(record_id)
    return sessions, videos


def _cycle_signature(
    student_id: int,
    session_ids: dict[int, list[int]],
    video_ids: dict[int, list[int]],
) -> str:
    sessions = ",".join(str(value) for value in session_ids.get(student_id, []))
    videos = ",".join(str(value) for value in video_ids.get(student_id, []))
    return f"sessions:{sessions}|videos:{videos}"


def _current_cycle_signature(s, student_id: int) -> str:
    session_ids, video_ids = _cycle_ids_maps(s)
    return _cycle_signature(student_id, session_ids, video_ids)


def _current_countable_counts(s, student_id: int) -> tuple[int, int]:
    sessions = s.query(func.count(SessionModel.id)).filter(
        SessionModel.student_id == student_id,
        SessionModel.counted == True,  # noqa: E712
        SessionModel.is_free == False,  # noqa: E712
    ).scalar() or 0
    videos = s.query(func.count(Video.id)).filter(
        Video.student_id == student_id,
        Video.counted == True,  # noqa: E712
    ).scalar() or 0
    return int(sessions), int(videos)


def students_with_dues() -> List[Dict]:
    s = get_session()
    session_counts = counted_sessions_map()
    video_counts = counted_videos_map()
    session_ids, video_ids = _cycle_ids_maps(s)
    pending_invoices = (
        s.query(Invoice)
        .filter(Invoice.is_paid == False)  # noqa: E712
        .order_by(Invoice.issued_at.desc(), Invoice.id.desc())
        .all()
    )
    pending_by_student: dict[int, list[Invoice]] = {}
    for invoice in pending_invoices:
        pending_by_student.setdefault(invoice.student_id, []).append(invoice)
    result = []
    for student in s.query(Student).filter(Student.is_active == True).all():  # noqa: E712
        count = session_counts.get(student.id, 0)
        cycle = student.sessions_per_cycle or 0
        if cycle > 0 and count >= cycle:
            vids = video_counts.get(student.id, 0)
            amount = round(count * float(student.price_per_session or 0), 2)
            signature = _cycle_signature(student.id, session_ids, video_ids)
            invoice_id = None
            student_pending = pending_by_student.get(student.id, [])
            legacy_invoice_ids = [
                invoice.id for invoice in student_pending if _is_legacy_unlinked(invoice)
            ]
            for invoice in student_pending:
                if (
                    invoice.cycle_signature == signature
                    and invoice.sessions_count == count
                    and invoice.videos_count == vids
                    and round(float(invoice.amount), 2) == amount
                ):
                    invoice_id = invoice.id
                    break
            result.append({
                "student": student,
                "sessions": count,
                "videos": vids,
                "amount": amount,
                "invoice_id": invoice_id,
                "legacy_invoice_ids": legacy_invoice_ids,
            })
    return result


def _reset_cycle_in_session(s, student_id: int) -> None:
    s.query(SessionModel).filter(
        SessionModel.student_id == student_id,
        SessionModel.counted == True,  # noqa: E712
    ).update({"counted": False})
    s.query(Video).filter(
        Video.student_id == student_id,
        Video.counted == True,  # noqa: E712
    ).update({"counted": False})


def reset_cycle(student_id: int) -> None:
    with session_scope() as s:
        unpaid = s.query(Invoice).filter(
            Invoice.student_id == student_id,
            Invoice.is_paid == False,  # noqa: E712
        ).first()
        if unpaid:
            if _is_legacy_unlinked(unpaid):
                raise LegacyInvoiceReconciliationRequired(
                    "توجد فاتورة قديمة غير مرتبطة. استخدم «تسوية فاتورة قديمة» أولاً."
                )
            raise UnpaidInvoiceError(
                "لا يمكن تصفير الدورة قبل تحصيل الفاتورة غير المدفوعة."
            )
        _reset_cycle_in_session(s, student_id)


def collect_payment_and_reset(
    invoice_id: int, student_id: Optional[int] = None
) -> Invoice:
    """Mark one invoice paid and reset its student's activity atomically."""
    with session_scope() as s:
        invoice = s.get(Invoice, invoice_id)
        if invoice is None:
            raise ValueError("الفاتورة غير موجودة")
        if student_id is not None and invoice.student_id != student_id:
            raise ValueError("الفاتورة لا تخص هذا الطالب")
        if invoice.is_paid:
            raise ValueError("تم تحصيل هذه الفاتورة من قبل")
        legacy_unlinked = s.query(Invoice.id).filter(
            *_legacy_unlinked_filter(invoice.student_id),
            Invoice.id != invoice.id,
        ).first()
        if legacy_unlinked:
            raise LegacyInvoiceReconciliationRequired(
                "سوِّ الفاتورة القديمة غير المرتبطة قبل تحصيل الدورة الحالية."
            )
        current_signature = _current_cycle_signature(s, invoice.student_id)
        if not invoice.cycle_signature or invoice.cycle_signature != current_signature:
            raise ValueError("الفاتورة لا تطابق الدورة الحالية للطالب")
        invoice.is_paid = True
        invoice.paid_at = datetime.now()
        _reset_cycle_in_session(s, invoice.student_id)
    return invoice


def record_invoice(
    student_id: int,
    sessions_count: int,
    videos_count: int,
    amount: float,
    pdf_path: str,
    notes: str = "",
    is_paid: bool = False,
) -> Invoice:
    with session_scope() as s:
        signature = _current_cycle_signature(s, student_id)
        legacy_unlinked = s.query(Invoice.id).filter(
            *_legacy_unlinked_filter(student_id)
        ).first()
        if legacy_unlinked:
            raise LegacyInvoiceReconciliationRequired(
                "سوِّ الفاتورة القديمة غير المرتبطة قبل إصدار فاتورة جديدة."
            )
        duplicate = s.query(Invoice.id).filter(
            Invoice.student_id == student_id,
            Invoice.cycle_signature == signature,
            Invoice.is_paid == False,  # noqa: E712
        ).first()
        if duplicate:
            raise DuplicateInvoiceError(
                "توجد فاتورة غير مدفوعة صادرة بالفعل لهذه الدورة."
            )
        invoice = Invoice(
            student_id=student_id,
            issued_at=datetime.now(),
            sessions_count=sessions_count,
            videos_count=videos_count,
            amount=amount,
            pdf_path=pdf_path,
            notes=notes,
            cycle_signature=signature,
            is_paid=is_paid,
            paid_at=datetime.now() if is_paid else None,
        )
        s.add(invoice)
        s.flush()
    return invoice


def list_invoices(only_paid: Optional[bool] = None) -> List[Invoice]:
    s = get_session()
    q = s.query(Invoice)
    if only_paid is True:
        q = q.filter(Invoice.is_paid == True)  # noqa: E712
    elif only_paid is False:
        q = q.filter(Invoice.is_paid == False)  # noqa: E712
    return q.order_by(Invoice.issued_at.desc()).all()


def mark_invoice_paid(invoice_id: int, paid: bool = True) -> Optional[Invoice]:
    with session_scope() as s:
        inv = s.get(Invoice, invoice_id)
        if not inv:
            return None
        if paid and not inv.is_paid and _is_legacy_unlinked(inv):
            raise LegacyInvoiceReconciliationRequired(
                "استخدم «تسوية فاتورة قديمة» لتسجيل دفع هذه الفاتورة."
            )
        inv.is_paid = paid
        inv.paid_at = datetime.now() if paid else None
    return inv


def delete_invoice(invoice_id: int) -> bool:
    with session_scope() as s:
        inv = s.get(Invoice, invoice_id)
        if not inv:
            return False
        s.delete(inv)
    return True


def legacy_invoice_reconciliation_data(invoice_id: int) -> Optional[Dict]:
    s = get_session()
    invoice = s.get(Invoice, invoice_id)
    if invoice is None:
        return None
    current_sessions, current_videos = _current_countable_counts(
        s, invoice.student_id
    )
    return {
        "invoice": invoice,
        "current_sessions": current_sessions,
        "current_videos": current_videos,
        "counts_match": (
            current_sessions == int(invoice.sessions_count or 0)
            and current_videos == int(invoice.videos_count or 0)
        ),
        "is_legacy_unlinked": _is_legacy_unlinked(invoice),
    }


def mark_legacy_invoice_paid_only(invoice_id: int) -> Invoice:
    with session_scope() as s:
        invoice = s.get(Invoice, invoice_id)
        if invoice is None:
            raise ValueError("الفاتورة غير موجودة")
        if invoice.is_paid:
            raise ValueError("تم تحصيل هذه الفاتورة من قبل")
        if not _is_legacy_unlinked(invoice):
            raise ValueError("هذه الفاتورة مرتبطة بدورة وليست فاتورة قديمة غير مرتبطة")
        invoice.is_paid = True
        invoice.paid_at = datetime.now()
    return invoice


def reconcile_legacy_invoice_and_reset(
    invoice_id: int, student_id: Optional[int] = None
) -> Invoice:
    """Link a matching legacy invoice, collect it, and reset atomically."""
    with session_scope() as s:
        invoice = s.get(Invoice, invoice_id)
        if invoice is None:
            raise ValueError("الفاتورة غير موجودة")
        if student_id is not None and invoice.student_id != student_id:
            raise ValueError("الفاتورة لا تخص هذا الطالب")
        if invoice.is_paid:
            raise ValueError("تم تحصيل هذه الفاتورة من قبل")
        if not _is_legacy_unlinked(invoice):
            raise ValueError("تم ربط هذه الفاتورة بدورة من قبل")
        current_sessions, current_videos = _current_countable_counts(
            s, invoice.student_id
        )
        if (
            current_sessions != int(invoice.sessions_count or 0)
            or current_videos != int(invoice.videos_count or 0)
        ):
            raise LegacyInvoiceCountMismatchError(
                "أعداد الدورة الحالية لا تطابق أعداد الفاتورة القديمة. "
                "يمكن تسجيل الدفع فقط دون تغيير النشاط."
            )
        invoice.cycle_signature = _current_cycle_signature(s, invoice.student_id)
        invoice.is_paid = True
        invoice.paid_at = datetime.now()
        _reset_cycle_in_session(s, invoice.student_id)
    return invoice


def backup_database() -> Path:
    """Create and validate a complete, DB-only online SQLite backup."""
    return online_backup(config.DB_PATH, config.BACKUPS_DIR)


def reset_all_activity(keep_students: bool = True) -> Tuple[Path, Dict[str, int]]:
    """Hard-reset: wipe sessions / videos / invoices to start a fresh period.

    Creates a verified local SQLite database backup before deletion. The backup
    remains local, must itself be protected, and excludes generated PDF/XLSX exports.

    When keep_students=True (default), student records (names, prices, zoom
    links, schedules) stay intact — only their counted activity disappears.
    When keep_students=False, students are deleted too (full factory reset).

    Returns (backup_path, counts_removed).
    """
    backup_path = backup_database()

    with session_scope() as s:
        counts = {
            "sessions": s.query(SessionModel).count(),
            "videos": s.query(Video).count(),
            "invoices": s.query(Invoice).count(),
            "students": 0,
        }
        # Delete in FK-safe order (children first).
        s.query(SessionModel).delete(synchronize_session=False)
        s.query(Video).delete(synchronize_session=False)
        s.query(Invoice).delete(synchronize_session=False)
        if not keep_students:
            counts["students"] = s.query(Student).count()
            s.query(Student).delete(synchronize_session=False)
    return backup_path, counts


def monthly_stats(year: int, month: int) -> Dict:
    s = get_session()
    from sqlalchemy import extract, func

    total_income = s.query(func.coalesce(func.sum(Invoice.amount), 0.0)).filter(
        extract("year", Invoice.issued_at) == year,
        extract("month", Invoice.issued_at) == month,
    ).scalar() or 0.0

    paid_income = s.query(func.coalesce(func.sum(Invoice.amount), 0.0)).filter(
        extract("year", Invoice.issued_at) == year,
        extract("month", Invoice.issued_at) == month,
        Invoice.is_paid == True,  # noqa: E712
    ).scalar() or 0.0

    total_sessions = s.query(func.count(SessionModel.id)).filter(
        extract("year", SessionModel.session_date) == year,
        extract("month", SessionModel.session_date) == month,
    ).scalar() or 0

    top_students = s.query(
        Student.name,
        func.count(SessionModel.id).label("c"),
    ).join(SessionModel, SessionModel.student_id == Student.id).filter(
        extract("year", SessionModel.session_date) == year,
        extract("month", SessionModel.session_date) == month,
    ).group_by(Student.id).order_by(func.count(SessionModel.id).desc()).limit(5).all()

    return {
        "income": float(total_income),
        "paid_income": float(paid_income),
        "pending_income": float(total_income - paid_income),
        "sessions": int(total_sessions),
        "top_students": [(n, int(c)) for n, c in top_students],
    }

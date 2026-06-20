import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from app.config import DATA_DIR, DB_PATH
from app.db.engine import get_session
from app.db.models import Student, Session as SessionModel, Video, Invoice
from app.services.student_service import counted_sessions_map, counted_videos_map


def students_with_dues() -> List[Dict]:
    s = get_session()
    session_counts = counted_sessions_map()
    video_counts = counted_videos_map()
    result = []
    for student in s.query(Student).filter(Student.is_active == True).all():  # noqa: E712
        count = session_counts.get(student.id, 0)
        cycle = student.sessions_per_cycle or 0
        if cycle > 0 and count >= cycle:
            vids = video_counts.get(student.id, 0)
            result.append({
                "student": student,
                "sessions": count,
                "videos": vids,
                "amount": round(count * float(student.price_per_session or 0), 2),
            })
    return result


def reset_cycle(student_id: int) -> None:
    s = get_session()
    s.query(SessionModel).filter(
        SessionModel.student_id == student_id,
        SessionModel.counted == True,  # noqa: E712
    ).update({"counted": False})
    s.query(Video).filter(
        Video.student_id == student_id,
        Video.counted == True,  # noqa: E712
    ).update({"counted": False})
    s.commit()


def record_invoice(
    student_id: int,
    sessions_count: int,
    videos_count: int,
    amount: float,
    pdf_path: str,
    notes: str = "",
    is_paid: bool = False,
) -> Invoice:
    s = get_session()
    invoice = Invoice(
        student_id=student_id,
        issued_at=datetime.now(),
        sessions_count=sessions_count,
        videos_count=videos_count,
        amount=amount,
        pdf_path=pdf_path,
        notes=notes,
        is_paid=is_paid,
        paid_at=datetime.now() if is_paid else None,
    )
    s.add(invoice)
    s.commit()
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
    s = get_session()
    inv = s.get(Invoice, invoice_id)
    if not inv:
        return None
    inv.is_paid = paid
    inv.paid_at = datetime.now() if paid else None
    s.commit()
    return inv


def delete_invoice(invoice_id: int) -> bool:
    s = get_session()
    inv = s.get(Invoice, invoice_id)
    if not inv:
        return False
    s.delete(inv)
    s.commit()
    return True


def backup_database() -> Path:
    """Copy the current SQLite DB to data/backups/ with a timestamped name.
    Returns the backup file path."""
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"teacher_backup_{ts}.db"
    shutil.copy2(DB_PATH, target)
    return target


def reset_all_activity(keep_students: bool = True) -> Tuple[Path, Dict[str, int]]:
    """Hard-reset: wipe sessions / videos / invoices to start a fresh period.

    Always takes a timestamped DB backup first so nothing is ever truly lost.

    When keep_students=True (default), student records (names, prices, zoom
    links, schedules) stay intact — only their counted activity disappears.
    When keep_students=False, students are deleted too (full factory reset).

    Returns (backup_path, counts_removed).
    """
    backup_path = backup_database()

    s = get_session()
    counts = {
        "sessions": s.query(SessionModel).count(),
        "videos": s.query(Video).count(),
        "invoices": s.query(Invoice).count(),
        "students": 0,
    }
    # Delete in FK-safe order (children first)
    s.query(SessionModel).delete(synchronize_session=False)
    s.query(Video).delete(synchronize_session=False)
    s.query(Invoice).delete(synchronize_session=False)
    if not keep_students:
        counts["students"] = s.query(Student).count()
        s.query(Student).delete(synchronize_session=False)
    s.commit()
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

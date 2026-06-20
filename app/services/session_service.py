from datetime import datetime
from typing import List, Optional

from app.db.engine import get_session
from app.db.models import Session as SessionModel, Video


def add_session(
    student_id: int,
    when: Optional[datetime] = None,
    notes: str = "",
    lesson_summary: str = "",
    is_free: bool = False,
) -> SessionModel:
    s = get_session()
    session = SessionModel(
        student_id=student_id,
        session_date=when or datetime.now(),
        counted=True,
        is_free=is_free,
        lesson_summary=lesson_summary,
        notes=notes,
    )
    s.add(session)
    s.commit()
    return session


def undo_last_session(student_id: int) -> Optional[dict]:
    s = get_session()
    last = (
        s.query(SessionModel)
        .filter(
            SessionModel.student_id == student_id,
            SessionModel.counted == True,  # noqa: E712
        )
        .order_by(SessionModel.id.desc())
        .first()
    )
    if not last:
        return None
    snapshot = {
        "id": last.id,
        "session_date": last.session_date,
        "notes": last.notes,
        "lesson_summary": last.lesson_summary,
        "is_free": last.is_free,
    }
    s.delete(last)
    s.commit()
    return snapshot


def update_session(session_id: int, **fields) -> Optional[SessionModel]:
    s = get_session()
    obj = s.get(SessionModel, session_id)
    if not obj:
        return None
    for k, v in fields.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    s.commit()
    return obj


def add_video(student_id: int, description: str = "", when: Optional[datetime] = None) -> Video:
    s = get_session()
    video = Video(
        student_id=student_id,
        sent_date=when or datetime.now(),
        description=description,
        counted=True,
    )
    s.add(video)
    s.commit()
    return video


def list_sessions(student_id: int, include_archived: bool = True) -> List[SessionModel]:
    s = get_session()
    q = s.query(SessionModel).filter(SessionModel.student_id == student_id)
    if not include_archived:
        q = q.filter(SessionModel.counted == True)  # noqa: E712
    return q.order_by(SessionModel.session_date.desc()).all()


def list_videos(student_id: int, include_archived: bool = True) -> List[Video]:
    s = get_session()
    q = s.query(Video).filter(Video.student_id == student_id)
    if not include_archived:
        q = q.filter(Video.counted == True)  # noqa: E712
    return q.order_by(Video.sent_date.desc()).all()


def delete_session(session_id: int) -> bool:
    s = get_session()
    obj = s.get(SessionModel, session_id)
    if not obj:
        return False
    s.delete(obj)
    s.commit()
    return True


def delete_video(video_id: int) -> bool:
    s = get_session()
    obj = s.get(Video, video_id)
    if not obj:
        return False
    s.delete(obj)
    s.commit()
    return True

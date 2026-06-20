import re
from typing import List, Optional, Tuple

from app.db.engine import get_session
from app.db.models import WhatsAppGroup


_INVITE_RE = re.compile(r"^https://chat\.whatsapp\.com/[A-Za-z0-9]+/?$")


def validate_invite_link(link: str) -> Tuple[bool, str]:
    """Return (is_valid, error_message_ar). Empty message when valid."""
    if not link or not link.strip():
        return False, "رابط الدعوة مطلوب"
    link = link.strip()
    if not _INVITE_RE.match(link):
        return False, "رابط الدعوة غير صالح — يجب أن يبدأ بـ https://chat.whatsapp.com/"
    return True, ""


def list_groups() -> List[WhatsAppGroup]:
    s = get_session()
    return s.query(WhatsAppGroup).order_by(WhatsAppGroup.name).all()


def get_group(group_id: int) -> Optional[WhatsAppGroup]:
    return get_session().get(WhatsAppGroup, group_id)


def create_group(name: str, invite_link: str, notes: str = "") -> WhatsAppGroup:
    s = get_session()
    g = WhatsAppGroup(
        name=(name or "").strip(),
        invite_link=(invite_link or "").strip(),
        notes=(notes or "").strip(),
    )
    s.add(g)
    s.commit()
    return g


def update_group(
    group_id: int,
    *,
    name: Optional[str] = None,
    invite_link: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[WhatsAppGroup]:
    s = get_session()
    g = s.get(WhatsAppGroup, group_id)
    if not g:
        return None
    if name is not None:
        g.name = name.strip()
    if invite_link is not None:
        g.invite_link = invite_link.strip()
    if notes is not None:
        g.notes = notes.strip()
    s.commit()
    return g


def delete_group(group_id: int) -> bool:
    s = get_session()
    g = s.get(WhatsAppGroup, group_id)
    if not g:
        return False
    s.delete(g)
    s.commit()
    return True

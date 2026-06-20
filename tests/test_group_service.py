"""اختبارات خدمة مجموعات واتساب (التحقق من الروابط + CRUD)."""
import pytest

from app.services import group_service as gs


@pytest.mark.parametrize("link", [
    "https://chat.whatsapp.com/ABC123def456",
    "https://chat.whatsapp.com/ABC123def456/",
])
def test_valid_invite_links(link):
    ok, msg = gs.validate_invite_link(link)
    assert ok is True
    assert msg == ""


@pytest.mark.parametrize("link", [
    "",
    "   ",
    "http://chat.whatsapp.com/ABC123",       # http بدل https
    "https://wa.me/123456",                   # نطاق خاطئ
    "https://chat.whatsapp.com/",             # بلا كود
    "just text",
])
def test_invalid_invite_links(link):
    ok, _ = gs.validate_invite_link(link)
    assert ok is False


def test_group_crud():
    g = gs.create_group("مجموعة أولى", "https://chat.whatsapp.com/ABC123", "ملاحظة")
    assert g.id is not None
    assert len(gs.list_groups()) == 1

    gs.update_group(g.id, name="محدّثة")
    assert gs.get_group(g.id).name == "محدّثة"

    assert gs.delete_group(g.id) is True
    assert gs.list_groups() == []
    assert gs.delete_group(g.id) is False


def test_update_missing_group_returns_none():
    assert gs.update_group(99999, name="x") is None

"""اختبارات خدمة الإعدادات (تخزين key/value بصيغة JSON)."""
from app.services import settings_service as cfg
from app.db.engine import get_session
from app.db.models import Setting


def test_roundtrip_various_types():
    cfg.set_setting("a_dict", {"x": 1, "ع": "نص"})
    cfg.set_setting("a_list", [1, 2, 3])
    cfg.set_setting("a_str", "مرحبا")
    cfg.set_setting("a_bool", True)
    assert cfg.get_setting("a_dict") == {"x": 1, "ع": "نص"}
    assert cfg.get_setting("a_list") == [1, 2, 3]
    assert cfg.get_setting("a_str") == "مرحبا"
    assert cfg.get_setting("a_bool") is True


def test_missing_key_returns_default():
    assert cfg.get_setting("nope", "افتراضي") == "افتراضي"


def test_corrupt_value_returns_raw_string():
    s = get_session()
    s.add(Setting(key="bad", value="{not json"))
    s.commit()
    # لا ينهار — يعيد القيمة الخام
    assert cfg.get_setting("bad") == "{not json"


def test_theme_default_and_set():
    assert cfg.get_theme() == "light"
    cfg.set_theme("dark")
    assert cfg.get_theme() == "dark"


def test_templates_default_then_custom():
    assert len(cfg.get_templates()) >= 1  # القوالب الافتراضية
    cfg.save_templates([{"name": "خاص", "text": "نص"}])
    assert cfg.get_templates() == [{"name": "خاص", "text": "نص"}]


def test_notification_minutes_default_and_corrupt():
    assert cfg.get_notification_minutes() == 10
    cfg.set_setting("notification_minutes", "ليس رقماً")
    assert cfg.get_notification_minutes() == 10  # يعود للافتراضي بأمان


def test_notifications_enabled_default():
    assert cfg.get_notifications_enabled() is True
    cfg.set_notifications_enabled(False)
    assert cfg.get_notifications_enabled() is False

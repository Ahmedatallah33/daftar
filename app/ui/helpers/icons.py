"""Icon helper using qtawesome for professional vector icons.

Icons automatically adapt to the current theme's color.
"""
from functools import lru_cache

import qtawesome as qta
from PySide6.QtGui import QIcon


_THEME_COLOR = "#475569"


def set_default_color(hex_color: str):
    global _THEME_COLOR
    _THEME_COLOR = hex_color


def icon(name: str, color: str = None, size: int = None) -> QIcon:
    """Get an icon by name, e.g. 'fa5s.calendar-alt' or 'mdi6.home'.

    Defaults to the current theme's neutral color.
    """
    return _cached_icon(name, color or _THEME_COLOR, size)


@lru_cache(maxsize=256)
def _cached_icon(name: str, color: str, size: int | None) -> QIcon:
    # ``size`` remains part of the public cache key for callers that use it;
    # qtawesome applies the actual pixel size when QIcon.pixmap() is requested.
    return qta.icon(name, color=color)


ICONS = {
    "schedule": "fa5s.calendar-day",
    "students": "fa5s.users",
    "billing": "fa5s.money-check-alt",
    "invoices": "fa5s.file-invoice-dollar",
    "reports": "fa5s.chart-line",
    "manage": "fa5s.user-cog",
    "settings": "fa5s.cog",
    "theme_light": "fa5s.sun",
    "theme_dark": "fa5s.moon",
    "zoom": "fa5s.video",
    "whatsapp": "fa5b.whatsapp",
    "add": "fa5s.plus",
    "edit": "fa5s.edit",
    "delete": "fa5s.trash",
    "check": "fa5s.check",
    "x": "fa5s.times",
    "undo": "fa5s.undo",
    "pdf": "fa5s.file-pdf",
    "open": "fa5s.external-link-alt",
    "bell": "fa5s.bell",
    "play": "fa5s.play-circle",
    "money": "fa5s.dollar-sign",
    "calendar": "fa5s.calendar-alt",
    "clock": "fa5s.clock",
    "phone": "fa5s.phone",
    "search": "fa5s.search",
    "video": "fa5s.film",
    "chevron_down": "fa5s.chevron-down",
    "save": "fa5s.save",
    "empty_schedule": "fa5s.coffee",
    "empty_billing": "fa5s.smile",
    "info": "fa5s.info-circle",
    "warning": "fa5s.exclamation-triangle",
    "archive": "fa5s.archive",
    "gift": "fa5s.gift",
    "book": "fa5s.book-open",
    "user": "fa5s.user",
    "link": "fa5s.link",
    "note": "fa5s.sticky-note",
    "cycle": "fa5s.sync-alt",
    "days": "fa5s.calendar-week",
    "sun_morning": "fa5s.sun",
    "moon_evening": "fa5s.moon",
    "cancel": "fa5s.times-circle",
}


def nav_icon(key: str, color: str) -> QIcon:
    return icon(ICONS.get(key, key), color=color)


def btn_icon(key: str, color: str = None) -> QIcon:
    return icon(ICONS.get(key, key), color=color)

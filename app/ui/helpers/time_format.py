"""Arabic 12-hour time formatting helpers."""


def format_time_ar(hhmm: str) -> str:
    """'17:30' -> '5:30 م' ; '08:00' -> '8:00 ص'."""
    if not hhmm:
        return "—"
    try:
        h, m = hhmm.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return hhmm
    period = "م" if h >= 12 else "ص"
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{m:02d} {period}"


def format_hour_ar(hour_24: int) -> str:
    """24h hour -> '8 ص' / '12 م' / '1 م' / '11 م' / '12 ص' (midnight)."""
    period = "م" if hour_24 >= 12 else "ص"
    h12 = hour_24 % 12
    if h12 == 0:
        h12 = 12
    return f"{h12} {period}"

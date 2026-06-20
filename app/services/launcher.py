import re
import urllib.parse
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication


def _clean_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def open_zoom(zoom_link: str) -> bool:
    if not zoom_link:
        return False
    return QDesktopServices.openUrl(QUrl(zoom_link.strip()))


def open_whatsapp(phone: str, message: str = "") -> bool:
    number = _clean_phone(phone)
    if not number:
        return False
    text_param = f"&text={urllib.parse.quote(message)}" if message else ""
    native = QUrl(f"whatsapp://send?phone={number}{text_param}")
    if QDesktopServices.openUrl(native):
        return True
    web_text = f"?text={urllib.parse.quote(message)}" if message else ""
    return QDesktopServices.openUrl(QUrl(f"https://wa.me/{number}{web_text}"))


def open_path(path: str) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def copy_to_clipboard(text: str) -> bool:
    """Place text on the system clipboard. Returns True on success."""
    try:
        cb = QGuiApplication.clipboard()
        if cb is None:
            return False
        cb.setText(text or "")
        return True
    except Exception:
        return False


def open_whatsapp_group(invite_link: str, message_to_clipboard: str = "") -> bool:
    """Open a WhatsApp group via its invite link.

    WhatsApp URL schemes don't support group IDs directly, so:
      1) Copy the message (if any) to clipboard.
      2) Open the invite link → WhatsApp Desktop will prompt to open the group.
      3) User pastes (Ctrl+V) the template inside the group.
    """
    if not invite_link:
        return False
    link = invite_link.strip()
    if message_to_clipboard:
        copy_to_clipboard(message_to_clipboard)
    return QDesktopServices.openUrl(QUrl(link))


def render_template(template: str, **variables) -> str:
    result = template
    for key, value in variables.items():
        result = result.replace("{" + key + "}", str(value) if value is not None else "")
    return result

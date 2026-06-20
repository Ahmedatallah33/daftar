
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from app.config import STYLES_DIR
from app.services import settings_service
from app.ui.helpers import icons


LIGHT_ICON_COLOR = "#475569"
DARK_ICON_COLOR = "#CBD5E1"


class ThemeManager(QObject):
    theme_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self._theme = settings_service.get_theme()

    @property
    def theme(self) -> str:
        return self._theme

    def is_dark(self) -> bool:
        return self._theme == "dark"

    def icon_color(self) -> str:
        return DARK_ICON_COLOR if self.is_dark() else LIGHT_ICON_COLOR

    def accent_color(self) -> str:
        return "#818CF8" if self.is_dark() else "#6366F1"

    def nav_icon_color(self) -> str:
        return "#94A3B8" if self.is_dark() else "#A5B4FC"

    def nav_icon_color_active(self) -> str:
        return "#FFFFFF"

    # ---- Surface & text helpers (theme-aware) ----
    def surface_bg(self) -> str:
        """Background color of the main app surface."""
        return "#0F172A" if self.is_dark() else "#F5F7FB"

    def card_bg(self) -> str:
        """Background color of raised cards / dialogs."""
        return "#1E293B" if self.is_dark() else "#FFFFFF"

    def subtle_bg(self) -> str:
        """Muted surface (footers, headers inside cards)."""
        return "#172033" if self.is_dark() else "#FAFAFA"

    def text_color(self) -> str:
        return "#E2E8F0" if self.is_dark() else "#0F172A"

    def muted_text_color(self) -> str:
        return "#94A3B8" if self.is_dark() else "#475569"

    def divider_color(self) -> str:
        return "#334155" if self.is_dark() else "#EEF2F6"

    def input_border(self) -> str:
        return "#334155" if self.is_dark() else "#CBD5E1"

    def input_border_hover(self) -> str:
        return "#64748B" if self.is_dark() else "#94A3B8"

    def apply(self, app: QApplication, theme: str = None):
        if theme:
            self._theme = theme
            settings_service.set_theme(theme)
        qss_file = STYLES_DIR / (
            "app_dark.qss" if self._theme == "dark" else "app_light.qss"
        )
        if qss_file.exists():
            app.setStyleSheet(qss_file.read_text(encoding="utf-8"))
        icons.set_default_color(self.icon_color())
        self.theme_changed.emit(self._theme)

    def toggle(self, app: QApplication):
        self.apply(app, "light" if self._theme == "dark" else "dark")


theme_manager = ThemeManager()

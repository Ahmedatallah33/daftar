from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QLabel, QStackedWidget, QButtonGroup, QFrame, QSystemTrayIcon,
    QApplication, QDialog
)

from app.config import ICONS_DIR
from app.cloud.supabase_auth import SupabaseEmailOtpAuth
from app.identity.models import AccountState
from app.ui.helpers.icons import icon, ICONS
from app.ui.helpers.theme import theme_manager
from app.ui.pages.account_dialog import AccountDialog
from app.ui.pages.schedule_page import SchedulePage
from app.ui.pages.students_page import StudentsPage
from app.ui.pages.billing_page import BillingPage
from app.ui.pages.invoices_page import InvoicesPage
from app.ui.pages.reports_page import ReportsPage
from app.ui.pages.groups_page import GroupsPage
from app.ui.pages.manage_students import ManageStudentsDialog
from app.ui.pages.settings_dialog import SettingsDialog
from app.services.notification_service import NotificationService


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Teacher Hub — إدارة الحصص")
        self.resize(1360, 860)
        self.setLayoutDirection(Qt.RightToLeft)
        self.account_auth = SupabaseEmailOtpAuth()

        app_icon_path = ICONS_DIR / "app.ico"
        if app_icon_path.exists():
            self.setWindowIcon(QIcon(str(app_icon_path)))

        central = QWidget()
        central.setObjectName("CentralSurface")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = self._build_sidebar()
        root.addWidget(self.sidebar)

        main_column = QWidget()
        main_column.setObjectName("CentralSurface")
        mc_layout = QVBoxLayout(main_column)
        mc_layout.setContentsMargins(0, 0, 0, 0)
        mc_layout.setSpacing(0)

        self.topbar = self._build_topbar()
        mc_layout.addWidget(self.topbar)

        self.stack = QStackedWidget()
        self.schedule_page = SchedulePage(auto_refresh=False)
        self.students_page = StudentsPage(auto_refresh=False)
        self.billing_page = BillingPage(auto_refresh=False)
        self.invoices_page = InvoicesPage(auto_refresh=False)
        self.reports_page = ReportsPage(auto_refresh=False)
        self.groups_page = GroupsPage(auto_refresh=False)

        pages = (
            self.schedule_page,
            self.students_page,
            self.billing_page,
            self.invoices_page,
            self.reports_page,
            self.groups_page,
        )
        for page in pages:
            self.stack.addWidget(page)

        self._dirty = set(range(self.stack.count()))
        for index, page in enumerate(pages):
            if hasattr(page, "data_changed"):
                page.data_changed.connect(
                    lambda source_index=index: self._on_data_changed(source_index)
                )

        mc_layout.addWidget(self.stack, 1)

        root.addWidget(main_column, 1)

        self.tray = self._build_tray()

        self.notifier = NotificationService(self)
        self.notifier.upcoming_lesson.connect(self._on_upcoming_lesson)
        self.notifier.start()

        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(30_000)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start()

        theme_manager.theme_changed.connect(self._refresh_icons)

        self._switch_page(0)
        self._update_account_status()
        self._update_clock()

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Sidebar")
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        header = QWidget()
        header.setObjectName("SidebarHeader")
        hv = QVBoxLayout(header)
        hv.setContentsMargins(20, 22, 20, 18)
        hv.setSpacing(8)

        # Logo + brand row
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_row.setContentsMargins(0, 0, 0, 0)

        logo = QLabel()
        logo_path = ICONS_DIR / "app.ico"
        if logo_path.exists():
            pix = QIcon(str(logo_path)).pixmap(QSize(40, 40))
            logo.setPixmap(pix)
        logo.setFixedSize(40, 40)
        logo.setStyleSheet("background: transparent;")
        brand_row.addWidget(logo)

        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        brand_col.setContentsMargins(0, 0, 0, 0)
        brand = QLabel("Teacher Hub")
        brand.setObjectName("SidebarBrand")
        sub = QLabel("إدارة الحصص الأونلاين")
        sub.setObjectName("SidebarSubtitle")
        brand_col.addWidget(brand)
        brand_col.addWidget(sub)
        brand_row.addLayout(brand_col, 1)

        hv.addLayout(brand_row)
        v.addWidget(header)
        v.addSpacing(8)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self._nav_buttons = []
        nav_items = [
            ("schedule", "جدول اليوم", 0),
            ("students", "سجل الطلاب", 1),
            ("billing", "المستحقات", 2),
            ("invoices", "سجل الفواتير", 3),
            ("reports", "التقارير", 4),
            ("whatsapp", "مجموعات واتساب", 5),
        ]
        for icon_key, text, page_idx in nav_items:
            btn = QPushButton(f"  {text}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setIconSize(QSize(18, 18))
            btn.setIcon(icon(ICONS[icon_key], color=theme_manager.nav_icon_color()))
            btn.clicked.connect(lambda _=False, i=page_idx: self._switch_page(i))
            self.nav_group.addButton(btn, page_idx)
            v.addWidget(btn)
            self._nav_buttons.append((btn, icon_key))

        v.addStretch(1)

        manage_btn = QPushButton("  إدارة الطلاب")
        manage_btn.setObjectName("SidebarGhost")
        manage_btn.setIconSize(QSize(16, 16))
        manage_btn.setIcon(icon(ICONS["manage"], color=theme_manager.nav_icon_color()))
        manage_btn.clicked.connect(self._open_manage_students)
        v.addWidget(manage_btn)
        self._manage_btn = manage_btn

        settings_btn = QPushButton("  الإعدادات")
        settings_btn.setObjectName("SidebarGhost")
        settings_btn.setIconSize(QSize(16, 16))
        settings_btn.setIcon(icon(ICONS["settings"], color=theme_manager.nav_icon_color()))
        settings_btn.clicked.connect(self._open_settings)
        v.addWidget(settings_btn)
        self._settings_btn = settings_btn

        theme_btn = QPushButton("  تبديل الثيم")
        theme_btn.setObjectName("SidebarGhost")
        theme_btn.setIconSize(QSize(16, 16))
        theme_btn.setIcon(icon(
            ICONS["theme_dark"] if not theme_manager.is_dark() else ICONS["theme_light"],
            color=theme_manager.nav_icon_color()
        ))
        theme_btn.clicked.connect(self._toggle_theme)
        v.addWidget(theme_btn)
        self._theme_btn = theme_btn

        footer = QLabel("v2.0  •  Teacher Hub")
        footer.setObjectName("SidebarFooterLabel")
        footer.setAlignment(Qt.AlignCenter)
        v.addWidget(footer)

        return side

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(64)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(30, 0, 30, 0)
        layout.setSpacing(12)

        self.topbar_title = QLabel()
        self.topbar_title.setObjectName("TopBarTitle")
        layout.addWidget(self.topbar_title)

        layout.addStretch(1)

        self.topbar_date = QLabel()
        self.topbar_date.setObjectName("TopBarDate")
        layout.addWidget(self.topbar_date)

        self.account_status = QLabel()
        self.account_status.setObjectName("TopBarDate")
        layout.addWidget(self.account_status)

        self.account_btn = QPushButton("تسجيل الدخول")
        self.account_btn.setObjectName("GhostBtn")
        self.account_btn.clicked.connect(self._open_account_dialog)
        layout.addWidget(self.account_btn)

        return bar

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        app_icon_path = ICONS_DIR / "app.ico"
        if app_icon_path.exists():
            tray_icon = QIcon(str(app_icon_path))
        else:
            tray_icon = icon(ICONS["bell"], color=theme_manager.accent_color())
        tray = QSystemTrayIcon(tray_icon, self)
        tray.setToolTip("Teacher Hub")
        tray.show()
        return tray

    def _update_clock(self):
        ARABIC_WEEKDAYS = {
            0: "الإثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        now = datetime.now()
        day = ARABIC_WEEKDAYS[now.weekday()]
        self.topbar_date.setText(f"{day} • {now.strftime('%Y-%m-%d')}  •  {now.strftime('%H:%M')}")

    def _switch_page(self, index: int) -> None:
        titles = {
            0: "جدول اليوم",
            1: "سجل الطلاب",
            2: "المستحقات والفواتير",
            3: "سجل الفواتير",
            4: "التقارير والإحصائيات",
            5: "مجموعات واتساب",
        }
        self.stack.setCurrentIndex(index)
        btn = self.nav_group.button(index)
        if btn:
            btn.setChecked(True)
        self.topbar_title.setText(titles.get(index, ""))
        if index in self._dirty:
            self._refresh_page(index)
        self._refresh_icons()

    def _open_manage_students(self) -> None:
        dialog = ManageStudentsDialog(self)
        dialog.exec()
        self._on_data_changed()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.exec()

    def _open_account_dialog(self) -> None:
        dialog = AccountDialog(self.account_auth, self)
        if dialog.exec() == QDialog.Accepted:
            self._update_account_status()

    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        theme_manager.toggle(app)

    def _update_account_status(self) -> None:
        state = self.account_auth.current_state
        if state == AccountState.SIGNED_IN_ONLINE:
            self.account_status.setText("الحساب: متصل")
            self.account_btn.setText("الحساب")
        elif state == AccountState.SIGN_IN_PENDING:
            self.account_status.setText("الحساب: بانتظار الرمز")
            self.account_btn.setText("إكمال الدخول")
        else:
            self.account_status.setText("الحساب: غير مسجل")
            self.account_btn.setText("تسجيل الدخول")

    def _refresh_icons(self, *args) -> None:
        nav_color = theme_manager.nav_icon_color()
        active_color = theme_manager.nav_icon_color_active()
        for btn, key in self._nav_buttons:
            btn.setIcon(icon(ICONS[key], color=active_color if btn.isChecked() else nav_color))
        self._manage_btn.setIcon(icon(ICONS["manage"], color=nav_color))
        self._settings_btn.setIcon(icon(ICONS["settings"], color=nav_color))
        theme_icon_key = "theme_light" if theme_manager.is_dark() else "theme_dark"
        self._theme_btn.setIcon(icon(ICONS[theme_icon_key], color=nav_color))
        if self.tray:
            app_icon_path = ICONS_DIR / "app.ico"
            if app_icon_path.exists():
                self.tray.setIcon(QIcon(str(app_icon_path)))
            else:
                self.tray.setIcon(icon(ICONS["bell"], color=theme_manager.accent_color()))

    def _refresh_page(self, index: int) -> None:
        page = self.stack.widget(index)
        if hasattr(page, "refresh"):
            page.refresh()
        self._dirty.discard(index)

    def _on_data_changed(self, _source_index: int | None = None) -> None:
        self._dirty.update(range(self.stack.count()))
        current = self.stack.currentIndex()
        if current >= 0:
            self._refresh_page(current)

    def _on_upcoming_lesson(self, student_name: str, lesson_time: str, minutes: int):
        title = "تذكير بحصة قادمة"
        if minutes <= 0:
            message = f"حصة {student_name} الآن ({lesson_time})"
        else:
            message = f"حصة {student_name} خلال {minutes} دقيقة ({lesson_time})"
        if self.tray:
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 8000)

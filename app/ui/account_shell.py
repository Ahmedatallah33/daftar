from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QLabel,
    QDialog,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.activation import ActivationCoordinator
from app.config import ICONS_DIR
from app.cloud.supabase_auth import SupabaseEmailOtpAuth
from app.cloud.supabase_workspace_repository import SupabaseWorkspaceRepository, WorkspaceLookupError
from app.identity.models import AccountState
from app.ui.pages.account_dialog import AccountDialog
from app.ui.helpers.worker import run_in_background, set_background_operations_enabled


class AccountShell(QMainWindow):
    """Signed-out startup shell; it deliberately exposes no operational pages."""

    def __init__(self, auth: SupabaseEmailOtpAuth | None = None):
        super().__init__()
        self.auth = auth or SupabaseEmailOtpAuth()
        set_background_operations_enabled(True)
        self._main_window = None
        self._activation_coordinator = ActivationCoordinator(
            SupabaseWorkspaceRepository(lambda: self.auth.authenticated_client)
        )
        self.setWindowTitle("Daftar — تسجيل الدخول")
        self.resize(720, 420)
        self.setLayoutDirection(Qt.RightToLeft)

        icon_path = ICONS_DIR / "app.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        central = QWidget()
        central.setObjectName("CentralSurface")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(48, 42, 48, 42)
        layout.setSpacing(16)

        self.title = QLabel("دفتر")
        self.title.setObjectName("PageTitle")
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title)

        subtitle = QLabel(
            "سجّل الدخول للمتابعة. لن تُفتح بيانات الطلاب أو الجداول أو الفواتير "
            "قبل اختيار حساب ومساحة عمل."
        )
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(subtitle)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setObjectName("PageSubtitle")
        layout.addWidget(self.status_label)

        self.sign_in_btn = QPushButton("تسجيل الدخول")
        self.sign_in_btn.setObjectName("SuccessBtn")
        self.sign_in_btn.clicked.connect(self.open_account_dialog)
        layout.addWidget(self.sign_in_btn, alignment=Qt.AlignCenter)

        note = QLabel(
            "بيانات Teacher Hub المحلية القديمة محفوظة كما هي، ولن تُعرض أو تُستورد "
            "إلا من خلال خطوة مطالبة/استيراد صريحة لاحقاً."
        )
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignCenter)
        note.setObjectName("StudentMeta")
        layout.addStretch(1)
        layout.addWidget(note)

        self._refresh_status()

    def open_account_dialog(self) -> None:
        dialog = AccountDialog(self.auth, self)
        if dialog.exec() == QDialog.Accepted:
            result = dialog.verification_result
            identity = result.identity if result is not None else self.auth.authenticated_identity
            if identity is not None:
                self._start_activation(identity)
                return
        self._refresh_status()

    def _start_activation(self, identity) -> None:
        self._set_activation_busy(True, "جاري فتح مساحة العمل بأمان...")
        run_in_background(
            self,
            lambda: self._activation_coordinator.activate(identity),
            on_result=self._on_activation_success,
            on_error=self._on_activation_error,
            on_finished=lambda: self._set_activation_busy(False),
        )

    def _on_activation_success(self, activation_result) -> None:
        from app.ui.main_window import MainWindow

        self._main_window = MainWindow(
            auth=self.auth,
            activation_result=activation_result,
        )
        self._main_window.showNormal()
        self._main_window.raise_()
        self._main_window.activateWindow()
        self.hide()

    def _on_activation_error(self, error: Exception) -> None:
        self._activation_coordinator.rollback_partial_activation()
        self.auth.sign_out()
        if isinstance(error, WorkspaceLookupError):
            self.status_label.setText(str(error))
        else:
            self.status_label.setText(
                "تعذر فتح مساحة العمل بأمان. لم يتم فتح أي بيانات تشغيلية."
            )
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _set_activation_busy(self, busy: bool, message: str = "") -> None:
        self.sign_in_btn.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

    def _refresh_status(self) -> None:
        state = self.auth.current_state
        if state == AccountState.SIGNED_IN_ONLINE:
            self.status_label.setText(
                "تم إثبات الحساب مبدئياً. فتح البيانات المحلية ينتظر اختيار "
                "مساحة عمل وتفعيل سياق حساب آمن في مرحلة لاحقة."
            )
            self.sign_in_btn.setText("الحساب")
        elif state == AccountState.SIGN_IN_PENDING:
            self.status_label.setText("تم إرسال رمز الدخول. أكمل التأكيد للمتابعة.")
            self.sign_in_btn.setText("إكمال تسجيل الدخول")
        else:
            self.status_label.setText("الحالة: لم يتم تسجيل الدخول.")
            self.sign_in_btn.setText("تسجيل الدخول")

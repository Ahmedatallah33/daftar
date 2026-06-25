from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QDialog,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.activation import ActivationCoordinator
from app.cloud.supabase_auth import (
    SupabaseAuthCredentialMalformedError,
    SupabaseAuthCredentialRejectedError,
    SupabaseAuthCredentialUnavailableError,
    SupabaseAuthFlowError,
    SupabaseAuthMissingConfigError,
    SupabaseAuthProviderUnavailableError,
    SupabaseEmailOtpAuth,
)
from app.cloud.supabase_workspace_repository import (
    SupabaseWorkspaceRepository,
    WorkspaceLookupError,
    WorkspaceMembership,
    WorkspaceSelectionError,
)
from app.config import ICONS_DIR
from app.identity.models import AccountState
from app.ui.helpers.worker import run_in_background, set_background_operations_enabled
from app.ui.pages.account_dialog import AccountDialog
from app.ui.pages.workspace_picker_dialog import WorkspacePickerDialog


class AccountShell(QMainWindow):
    """Signed-out startup shell; it deliberately exposes no operational pages."""

    def __init__(self, auth: SupabaseEmailOtpAuth | None = None):
        super().__init__()
        self.auth = auth or SupabaseEmailOtpAuth()
        set_background_operations_enabled(True)
        self._main_window = None
        self._refresh_in_progress = False
        self._activation_in_progress = False
        self._workspace_lookup_in_progress = False
        self._workspace_picker_open = False
        self._continue_reference = self._discover_single_remembered_session()
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

        entry_row = QHBoxLayout()
        entry_row.addStretch(1)

        self.continue_btn = QPushButton("متابعة")
        self.continue_btn.setObjectName("SuccessBtn")
        self.continue_btn.clicked.connect(self.continue_remembered_session)
        entry_row.addWidget(self.continue_btn)

        self.sign_in_btn = QPushButton("تسجيل الدخول")
        self.sign_in_btn.setObjectName("SuccessBtn")
        self.sign_in_btn.clicked.connect(self.open_account_dialog)
        entry_row.addWidget(self.sign_in_btn)
        entry_row.addStretch(1)
        layout.addLayout(entry_row)

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

    def continue_remembered_session(self) -> None:
        if self._flow_busy() or self._continue_reference is None:
            return
        self._refresh_in_progress = True
        self._set_entry_busy(True, "جاري متابعة جلسة الدخول بأمان...")
        run_in_background(
            self,
            lambda: self.auth.refresh_remembered_session(self._continue_reference),
            on_result=self._on_reentry_success,
            on_error=self._on_reentry_error,
        )

    def _on_reentry_success(self, result) -> None:
        self._refresh_in_progress = False
        self._continue_reference = None
        self._sync_continue_button()
        self._start_workspace_lookup(result.identity)

    def _on_reentry_error(self, error: Exception) -> None:
        self._refresh_in_progress = False
        if isinstance(
            error,
            (
                SupabaseAuthCredentialMalformedError,
                SupabaseAuthCredentialRejectedError,
                SupabaseAuthCredentialUnavailableError,
            ),
        ):
            self._continue_reference = self._discover_single_remembered_session()
        elif not isinstance(
            error,
            (SupabaseAuthMissingConfigError, SupabaseAuthProviderUnavailableError),
        ):
            self._continue_reference = self._discover_single_remembered_session()
        self._set_entry_busy(False)
        if isinstance(error, SupabaseAuthFlowError):
            self.status_label.setText(str(error))
        else:
            self.status_label.setText(
                "تعذرت متابعة جلسة الدخول المحفوظة. لم تُفتح أي بيانات تشغيلية."
            )
        self._sync_continue_button()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_account_dialog(self) -> None:
        if self._flow_busy():
            return
        identity = getattr(self.auth, "authenticated_identity", None)
        if identity is not None:
            self._start_workspace_lookup(identity)
            return
        dialog = AccountDialog(self.auth, self)
        if dialog.exec() == QDialog.Accepted:
            result = dialog.verification_result
            identity = result.identity if result is not None else self.auth.authenticated_identity
            if identity is not None:
                self._start_workspace_lookup(identity)
                return
        self._refresh_status()

    def _start_workspace_lookup(self, identity) -> None:
        if self._flow_busy():
            return
        self._workspace_lookup_in_progress = True
        self._set_entry_busy(True, "جاري التحقق من مساحات العمل المصرح بها...")
        run_in_background(
            self,
            lambda: self._activation_coordinator.list_authorized_workspaces(identity),
            on_result=lambda memberships: self._on_workspaces_loaded(identity, memberships),
            on_error=self._on_workspace_lookup_error,
        )

    def _on_workspaces_loaded(
        self,
        identity,
        memberships: tuple[WorkspaceMembership, ...],
    ) -> None:
        self._workspace_lookup_in_progress = False
        if len(memberships) == 1:
            self._start_activation(identity, memberships[0])
            return
        if len(memberships) > 1:
            self._show_workspace_picker(identity, memberships)
            return
        self._on_workspace_lookup_error(
            WorkspaceSelectionError(
                "لا توجد مساحة عمل مفعلة لهذا الحساب. لم يتم فتح أي بيانات محلية."
            )
        )

    def _show_workspace_picker(
        self,
        identity,
        memberships: tuple[WorkspaceMembership, ...],
    ) -> None:
        if self._activation_in_progress or self._workspace_picker_open:
            return
        self._workspace_picker_open = True
        self._set_entry_busy(True, "اختر مساحة العمل للمتابعة.")
        dialog = WorkspacePickerDialog(memberships, self)
        result = dialog.exec()
        self._workspace_picker_open = False
        selected = dialog.selected_membership
        if result == QDialog.Accepted and selected is not None:
            self._start_activation(identity, selected)
            return
        self._set_entry_busy(False)
        self.sign_in_btn.setText("اختيار مساحة العمل")
        self.status_label.setText(
            "تم إلغاء اختيار مساحة العمل. لم يتم فتح أي بيانات تشغيلية."
        )
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_workspace_lookup_error(self, error: Exception) -> None:
        self._workspace_lookup_in_progress = False
        self._activation_coordinator.rollback_partial_activation()
        if not isinstance(error, WorkspaceLookupError):
            self.auth.sign_out()
        self._set_entry_busy(False)
        if isinstance(error, WorkspaceLookupError):
            self.status_label.setText(str(error))
        else:
            self.status_label.setText(
                "تعذر التحقق من مساحات العمل الآن. لم يتم فتح أي بيانات تشغيلية."
            )
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _start_activation(
        self,
        identity,
        selected_membership: WorkspaceMembership,
    ) -> None:
        if self._activation_in_progress:
            return
        self._activation_in_progress = True
        self._set_entry_busy(True, "جاري فتح مساحة العمل بأمان...")
        run_in_background(
            self,
            lambda: self._activation_coordinator.activate_workspace(
                identity,
                selected_membership,
            ),
            on_result=self._on_activation_success,
            on_error=self._on_activation_error,
            on_finished=self._on_activation_finished,
        )

    def _on_activation_success(self, activation_result) -> None:
        try:
            from app.ui.main_window import MainWindow

            main_window = MainWindow(
                auth=self.auth,
                activation_result=activation_result,
            )
            main_window.showNormal()
            main_window.raise_()
            main_window.activateWindow()
        except Exception:
            self._activation_coordinator.rollback_partial_activation()
            self.status_label.setText(
                "تعذر فتح الواجهة التشغيلية بأمان. لم يتم إبقاء أي قاعدة بيانات مفتوحة."
            )
            self.showNormal()
            self.raise_()
            self.activateWindow()
            return

        self._main_window = main_window
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

    def _on_activation_finished(self) -> None:
        self._activation_in_progress = False
        if self._main_window is None:
            self._set_entry_busy(False)

    def _flow_busy(self) -> bool:
        return (
            self._refresh_in_progress
            or self._workspace_lookup_in_progress
            or self._workspace_picker_open
            or self._activation_in_progress
        )

    def _set_entry_busy(self, busy: bool, message: str = "") -> None:
        self.sign_in_btn.setEnabled(not busy)
        self.continue_btn.setEnabled(not busy)
        if message:
            self.status_label.setText(message)
        self._sync_continue_button()

    def _refresh_status(self) -> None:
        state = self.auth.current_state
        if state == AccountState.SIGNED_IN_ONLINE:
            self.status_label.setText(
                "تم إثبات الحساب مبدئياً. فتح البيانات المحلية ينتظر اختيار مساحة عمل آمنة."
            )
            self.sign_in_btn.setText("الحساب")
        elif state == AccountState.SIGN_IN_PENDING:
            self.status_label.setText("تم إرسال رمز الدخول. أكمل التأكيد للمتابعة.")
            self.sign_in_btn.setText("إكمال تسجيل الدخول")
        else:
            self.status_label.setText("الحالة: لم يتم تسجيل الدخول.")
            self.sign_in_btn.setText("تسجيل الدخول")
        self._sync_continue_button()

    def _sync_continue_button(self) -> None:
        identity = getattr(self.auth, "authenticated_identity", None)
        self.continue_btn.setVisible(
            self._continue_reference is not None
            and identity is None
        )

    def _discover_single_remembered_session(self):
        discover = getattr(self.auth, "discover_remembered_sessions", None)
        if discover is None:
            return None
        try:
            references = tuple(discover())
        except Exception:
            return None
        return references[0] if len(references) == 1 else None

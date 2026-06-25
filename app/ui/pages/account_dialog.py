from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.cloud.supabase_auth import SupabaseAuthFlowError, SupabaseEmailOtpAuth
from app.ui.helpers.worker import run_in_background


class AccountDialog(QDialog):
    """Minimal Email OTP account dialog; no local app access is gated by it."""

    def __init__(self, auth: SupabaseEmailOtpAuth, parent=None):
        super().__init__(parent)
        self.auth = auth
        self._email_for_otp = ""
        self.verification_result = None
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("حساب Daftar")
        self.resize(460, 280)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        title = QLabel("تسجيل الدخول بالبريد الإلكتروني")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        intro = QLabel(
            "أدخل بريدك الإلكتروني لاستلام رمز دخول من 6 أرقام. "
            "يمكنك إغلاق هذه النافذة؛ لن تُفتح بياناتك المحلية قبل اختيار حساب آمن."
        )
        intro.setWordWrap(True)
        intro.setObjectName("PageSubtitle")
        root.addWidget(intro)

        self.email_step = QWidget()
        email_layout = QVBoxLayout(self.email_step)
        email_layout.setContentsMargins(0, 0, 0, 0)
        email_layout.setSpacing(8)
        email_layout.addWidget(QLabel("البريد الإلكتروني"))
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("teacher@example.com")
        self.email_edit.setClearButtonEnabled(True)
        email_layout.addWidget(self.email_edit)
        self.request_btn = QPushButton("إرسال رمز الدخول")
        self.request_btn.setObjectName("SuccessBtn")
        self.request_btn.clicked.connect(self._request_code)
        email_layout.addWidget(self.request_btn, alignment=Qt.AlignLeft)
        root.addWidget(self.email_step)

        self.otp_step = QWidget()
        otp_layout = QVBoxLayout(self.otp_step)
        otp_layout.setContentsMargins(0, 0, 0, 0)
        otp_layout.setSpacing(8)
        self.otp_hint = QLabel()
        self.otp_hint.setWordWrap(True)
        otp_layout.addWidget(self.otp_hint)
        otp_layout.addWidget(QLabel("رمز الدخول"))
        self.otp_edit = QLineEdit()
        self.otp_edit.setPlaceholderText("000000")
        self.otp_edit.setMaxLength(12)
        otp_layout.addWidget(self.otp_edit)
        otp_buttons = QHBoxLayout()
        self.verify_btn = QPushButton("تأكيد الرمز")
        self.verify_btn.setObjectName("SuccessBtn")
        self.verify_btn.clicked.connect(self._verify_code)
        self.resend_btn = QPushButton("إرسال رمز جديد")
        self.resend_btn.setObjectName("GhostBtn")
        self.resend_btn.clicked.connect(self._request_code)
        otp_buttons.addWidget(self.verify_btn)
        otp_buttons.addWidget(self.resend_btn)
        otp_buttons.addStretch(1)
        otp_layout.addLayout(otp_buttons)
        root.addWidget(self.otp_step)
        self.otp_step.hide()

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("PageSubtitle")
        root.addWidget(self.status_label)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_btn = QPushButton("إغلاق")
        self.close_btn.setObjectName("GhostBtn")
        self.close_btn.clicked.connect(self.reject)
        close_row.addWidget(self.close_btn)
        root.addLayout(close_row)

    def _request_code(self) -> None:
        email = self.email_edit.text()
        self._set_busy(True, "جارٍ إرسال الرمز...")
        run_in_background(
            self,
            lambda: self.auth.request_code(email),
            on_result=self._on_code_requested,
            on_error=self._on_error,
            on_finished=lambda: self._set_busy(False),
        )

    def _verify_code(self) -> None:
        email = self._email_for_otp or self.email_edit.text()
        otp = self.otp_edit.text()
        self._set_busy(True, "جارٍ تأكيد الرمز...")
        run_in_background(
            self,
            lambda: self.auth.verify_code(email, otp),
            on_result=self._on_code_verified,
            on_error=self._on_error,
            on_finished=lambda: self._set_busy(False),
        )

    def _on_code_requested(self, result) -> None:
        self._email_for_otp = result.email
        self.email_step.hide()
        self.otp_hint.setText(
            "تم إرسال رمز الدخول إن كان البريد صالحاً للاستقبال. "
            "أدخل الرمز المكوّن من 6 أرقام للمتابعة."
        )
        self.otp_step.show()
        self.otp_edit.setFocus()
        self.status_label.setText("")

    def _on_code_verified(self, result) -> None:
        self.verification_result = result
        self.status_label.setText(
            "تم تسجيل الدخول بنجاح. فتح البيانات المحلية ينتظر اختيار مساحة عمل آمنة."
        )
        self.accept()

    def _on_error(self, error: Exception) -> None:
        if isinstance(error, SupabaseAuthFlowError):
            self.status_label.setText(str(error))
            return
        self.status_label.setText(
            "تعذر إكمال تسجيل الدخول الآن. لم تُفتح أي بيانات تشغيلية."
        )

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.email_edit.setEnabled(not busy)
        self.otp_edit.setEnabled(not busy)
        self.request_btn.setEnabled(not busy)
        self.verify_btn.setEnabled(not busy)
        self.resend_btn.setEnabled(not busy)
        self.close_btn.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

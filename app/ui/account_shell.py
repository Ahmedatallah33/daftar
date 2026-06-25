from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import ICONS_DIR
from app.cloud.supabase_auth import SupabaseEmailOtpAuth
from app.identity.models import AccountState
from app.ui.pages.account_dialog import AccountDialog


class AccountShell(QMainWindow):
    """Signed-out startup shell; it deliberately exposes no operational pages."""

    def __init__(self, auth: SupabaseEmailOtpAuth | None = None):
        super().__init__()
        self.auth = auth or SupabaseEmailOtpAuth()
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
        dialog.exec()
        self._refresh_status()

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

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from app.cloud.supabase_workspace_repository import WorkspaceMembership


ROLE_LABELS = {
    "owner": "مالك مساحة العمل",
    "admin": "مسؤول",
    "member": "عضو",
}


class WorkspacePickerDialog(QDialog):
    """Signed-out shell workspace picker; it exposes no account identifiers."""

    def __init__(
        self,
        memberships: Sequence[WorkspaceMembership],
        parent=None,
    ):
        super().__init__(parent)
        self._selected_membership: WorkspaceMembership | None = None
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("اختيار مساحة العمل")
        self.resize(520, 360)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        title = QLabel("اختر مساحة العمل")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        intro = QLabel(
            "هذا الحساب مرتبط بأكثر من مساحة عمل. اختر مساحة واحدة لفتح بياناتها المحلية بأمان."
        )
        intro.setWordWrap(True)
        intro.setObjectName("PageSubtitle")
        root.addWidget(intro)

        self.workspace_list = QListWidget()
        self.workspace_list.setAlternatingRowColors(True)
        self.workspace_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.workspace_list.itemActivated.connect(self._accept_current_selection)
        root.addWidget(self.workspace_list, 1)

        for membership in memberships:
            item = QListWidgetItem(_membership_label(membership))
            item.setData(Qt.UserRole, membership)
            self.workspace_list.addItem(item)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        self.cancel_btn = QPushButton("إلغاء")
        self.cancel_btn.setObjectName("GhostBtn")
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)

        self.open_btn = QPushButton("فتح مساحة العمل")
        self.open_btn.setObjectName("SuccessBtn")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._accept_current_selection)
        buttons.addWidget(self.open_btn)

        root.addLayout(buttons)

        self.workspace_list.setFocus()

    @property
    def selected_membership(self) -> WorkspaceMembership | None:
        return self._selected_membership

    def _on_selection_changed(self) -> None:
        self.open_btn.setEnabled(self.workspace_list.currentItem() is not None)

    def _accept_current_selection(self) -> None:
        item = self.workspace_list.currentItem()
        if item is None:
            return
        membership = item.data(Qt.UserRole)
        if not isinstance(membership, WorkspaceMembership):
            return
        self._selected_membership = membership
        self.accept()


def _membership_label(membership: WorkspaceMembership) -> str:
    role = ROLE_LABELS.get(membership.role, "عضو")
    return f"{membership.display_name}\n{role}"

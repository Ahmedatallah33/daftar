from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QTextEdit, QMessageBox, QFrame, QProgressBar,
    QDialog, QLineEdit
)

from app.services.group_service import (
    list_groups, create_group, update_group, delete_group,
    validate_invite_link, get_group
)
from app.services.launcher import open_whatsapp_group, copy_to_clipboard
from app.services.settings_service import get_setting, set_setting


GROUP_TEMPLATE_KEY = "group_message_template"
DEFAULT_TEMPLATE = (
    "السلام عليكم\n\n"
    "تذكير بأن الحصة القادمة ستكون في موعدها المعتاد.\n"
    "لا تنسوا المراجعة.\n\n"
    "بالتوفيق 🌟"
)
BROADCAST_DELAY_MS = 1500  # Delay between opening each group
MAX_BROADCAST = 10  # Max groups per broadcast operation


class GroupFormDialog(QDialog):
    """Add/edit a WhatsApp group."""

    def __init__(self, parent=None, group=None):
        super().__init__(parent)
        self.group = group
        self.setWindowTitle("تعديل مجموعة" if group else "إضافة مجموعة")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Name
        layout.addWidget(QLabel("اسم المجموعة:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("مثال: مجموعة الصف الثالث")
        layout.addWidget(self.name_edit)

        # Invite link
        layout.addWidget(QLabel("رابط الدعوة:"))
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText("https://chat.whatsapp.com/XXXXXXXXXXX")
        layout.addWidget(self.link_edit)

        hint = QLabel(
            "💡 للحصول على رابط الدعوة: افتح المجموعة في واتساب → "
            "معلومات المجموعة → دعوة عبر الرابط → نسخ الرابط."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748B; font-size: 11px;")
        layout.addWidget(hint)

        # Notes
        layout.addWidget(QLabel("ملاحظات (اختياري):"))
        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(80)
        layout.addWidget(self.notes_edit)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        save = QPushButton("حفظ")
        save.setObjectName("PrimaryBtn")
        save.setDefault(True)
        save.clicked.connect(self._save)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

        if group:
            self.name_edit.setText(group.name)
            self.link_edit.setText(group.invite_link)
            self.notes_edit.setPlainText(group.notes or "")

    def _save(self):
        name = self.name_edit.text().strip()
        link = self.link_edit.text().strip()
        notes = self.notes_edit.toPlainText().strip()

        if not name:
            QMessageBox.warning(self, "خطأ", "اسم المجموعة مطلوب")
            return

        ok, err = validate_invite_link(link)
        if not ok:
            QMessageBox.warning(self, "خطأ", err)
            return

        if self.group:
            update_group(self.group.id, name=name, invite_link=link, notes=notes)
        else:
            create_group(name=name, invite_link=link, notes=notes)
        self.accept()


class GroupsPage(QWidget):
    data_changed = Signal()

    def __init__(self, auto_refresh: bool = True):
        super().__init__()
        self.setObjectName("CentralSurface")
        self._broadcast_queue: list = []
        self._broadcast_total = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(12)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("مجموعات واتساب")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)

        add_btn = QPushButton("+ إضافة مجموعة")
        add_btn.setObjectName("PrimaryBtn")
        add_btn.clicked.connect(self._add_group)
        title_row.addWidget(add_btn)
        layout.addLayout(title_row)

        subtitle = QLabel(
            "أرسل القوالب إلى مجموعاتك. اكتب الرسالة، حدّد المجموعات، ثم اضغط «بث». "
            "سيتم نسخ الرسالة للحافظة وفتح كل مجموعة — ألصقها بـ Ctrl+V."
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(6)

        # Split: message editor (top) + groups list (bottom)
        body = QHBoxLayout()
        body.setSpacing(16)

        # Left: message template
        msg_card = QFrame()
        msg_card.setObjectName("CardBg")
        msg_layout = QVBoxLayout(msg_card)
        msg_layout.setContentsMargins(16, 14, 16, 14)
        msg_layout.setSpacing(8)

        msg_header = QLabel("📝 نص الرسالة (يُنسخ للحافظة)")
        msg_header.setStyleSheet("font-size: 14px; font-weight: 700;")
        msg_layout.addWidget(msg_header)

        self.template_edit = QTextEdit()
        self.template_edit.setPlaceholderText("اكتب القالب هنا...")
        saved_template = get_setting(GROUP_TEMPLATE_KEY, DEFAULT_TEMPLATE)
        self.template_edit.setPlainText(saved_template)
        msg_layout.addWidget(self.template_edit, 1)

        save_tpl_btn = QPushButton("💾 حفظ القالب")
        save_tpl_btn.clicked.connect(self._save_template)
        msg_layout.addWidget(save_tpl_btn, 0, Qt.AlignLeft)

        body.addWidget(msg_card, 1)

        # Right: groups list + action buttons
        groups_card = QFrame()
        groups_card.setObjectName("CardBg")
        groups_layout = QVBoxLayout(groups_card)
        groups_layout.setContentsMargins(16, 14, 16, 14)
        groups_layout.setSpacing(8)

        groups_header_row = QHBoxLayout()
        groups_header = QLabel("👥 المجموعات")
        groups_header.setStyleSheet("font-size: 14px; font-weight: 700;")
        groups_header_row.addWidget(groups_header)
        groups_header_row.addStretch(1)
        self.count_label = QLabel("0 مجموعة")
        self.count_label.setStyleSheet("color: #64748B; font-size: 12px;")
        groups_header_row.addWidget(self.count_label)
        groups_layout.addLayout(groups_header_row)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self._edit_selected)
        groups_layout.addWidget(self.list, 1)

        # Row-level actions
        row_actions = QHBoxLayout()
        edit_btn = QPushButton("✎ تعديل")
        edit_btn.clicked.connect(self._edit_selected)
        del_btn = QPushButton("🗑 حذف")
        del_btn.clicked.connect(self._delete_selected)
        row_actions.addWidget(edit_btn)
        row_actions.addWidget(del_btn)
        row_actions.addStretch(1)
        groups_layout.addLayout(row_actions)

        # Main action buttons
        action_row = QHBoxLayout()
        open_one_btn = QPushButton("📋 انسخ وافتح المحددة")
        open_one_btn.setToolTip("نسخ القالب وفتح المجموعة المحددة فقط")
        open_one_btn.clicked.connect(self._copy_and_open_single)
        action_row.addWidget(open_one_btn)

        broadcast_btn = QPushButton("📢 بث إلى المحددات")
        broadcast_btn.setObjectName("PrimaryBtn")
        broadcast_btn.setToolTip(
            f"فتح كل المجموعات المحددة بالتتابع (حد أقصى {MAX_BROADCAST})"
        )
        broadcast_btn.clicked.connect(self._start_broadcast)
        action_row.addWidget(broadcast_btn)
        groups_layout.addLayout(action_row)

        # Progress bar (hidden until broadcast starts)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setFormat("%v / %m — %p%")
        groups_layout.addWidget(self.progress)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #6366F1; font-size: 12px;")
        self.progress_label.setVisible(False)
        groups_layout.addWidget(self.progress_label)

        body.addWidget(groups_card, 1)

        layout.addLayout(body, 1)

        if auto_refresh:
            self.refresh()

    # -----------------------------------------------------------------
    # Data refresh
    # -----------------------------------------------------------------
    def refresh(self):
        self.list.clear()
        groups = list_groups()
        self.count_label.setText(f"{len(groups)} مجموعة")
        if not groups:
            empty = QListWidgetItem("لا توجد مجموعات. أضف واحدة أولاً.")
            empty.setFlags(Qt.NoItemFlags)
            empty.setTextAlignment(Qt.AlignCenter)
            self.list.addItem(empty)
            return
        for g in groups:
            label = g.name
            if g.notes:
                label += f"  —  {g.notes[:40]}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, g.id)
            item.setToolTip(f"{g.name}\n{g.invite_link}\n{g.notes or ''}")
            self.list.addItem(item)

    # -----------------------------------------------------------------
    # Template
    # -----------------------------------------------------------------
    def _save_template(self):
        set_setting(GROUP_TEMPLATE_KEY, self.template_edit.toPlainText())
        QMessageBox.information(self, "تم", "تم حفظ القالب.")

    # -----------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------
    def _add_group(self):
        dlg = GroupFormDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.data_changed.emit()

    def _edit_selected(self):
        group_id = self._current_group_id()
        if group_id is None:
            return
        g = get_group(group_id)
        if not g:
            return
        dlg = GroupFormDialog(self, group=g)
        if dlg.exec() == QDialog.Accepted:
            self.data_changed.emit()

    def _delete_selected(self):
        group_id = self._current_group_id()
        if group_id is None:
            return
        g = get_group(group_id)
        if not g:
            return
        ans = QMessageBox.question(
            self,
            "تأكيد الحذف",
            f"هل تريد حذف المجموعة «{g.name}»؟",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans == QMessageBox.Yes:
            delete_group(group_id)
            self.data_changed.emit()

    def _current_group_id(self) -> int | None:
        item = self.list.currentItem()
        if item is None:
            return None
        gid = item.data(Qt.UserRole)
        return int(gid) if gid is not None else None

    # -----------------------------------------------------------------
    # Send / Broadcast
    # -----------------------------------------------------------------
    def _selected_groups(self) -> list:
        """Return list of WhatsAppGroup for all selected items."""
        out = []
        for item in self.list.selectedItems():
            gid = item.data(Qt.UserRole)
            if gid is None:
                continue
            g = get_group(int(gid))
            if g:
                out.append(g)
        return out

    def _current_message(self) -> str:
        return self.template_edit.toPlainText().strip()

    def _copy_and_open_single(self):
        selected = self._selected_groups()
        if not selected:
            QMessageBox.information(
                self, "تنبيه", "اختر مجموعة واحدة على الأقل من القائمة."
            )
            return
        if len(selected) > 1:
            QMessageBox.information(
                self,
                "تنبيه",
                "هذا الزر للمجموعة المحددة فقط. استخدم «بث» لعدة مجموعات.",
            )
            return
        msg = self._current_message()
        if not msg:
            ans = QMessageBox.question(
                self,
                "رسالة فارغة",
                "نص الرسالة فارغ. هل تريد الاستمرار؟",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ans == QMessageBox.No:
                return

        g = selected[0]
        ok = open_whatsapp_group(g.invite_link, msg)
        if ok:
            QMessageBox.information(
                self,
                "تم",
                "تم نسخ الرسالة إلى الحافظة وفتح المجموعة.\nألصقها داخل المجموعة بـ Ctrl+V.",
            )
        else:
            QMessageBox.warning(
                self,
                "خطأ",
                "تعذر فتح المجموعة. تحقق من صحة الرابط أو تثبيت واتساب.",
            )

    def _start_broadcast(self):
        selected = self._selected_groups()
        if not selected:
            QMessageBox.information(
                self, "تنبيه", "اختر مجموعة واحدة أو أكثر من القائمة."
            )
            return
        if len(selected) > MAX_BROADCAST:
            QMessageBox.warning(
                self,
                "حد أقصى",
                f"الحد الأقصى {MAX_BROADCAST} مجموعات في البث الواحد. "
                f"اخترت {len(selected)} — قلّل العدد.",
            )
            return

        msg = self._current_message()
        if not msg:
            ans = QMessageBox.question(
                self,
                "رسالة فارغة",
                "نص الرسالة فارغ. هل تريد الاستمرار؟",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ans == QMessageBox.No:
                return

        ans = QMessageBox.question(
            self,
            "تأكيد البث",
            f"سيتم فتح {len(selected)} مجموعة بالتتابع (بفاصل "
            f"{BROADCAST_DELAY_MS // 1000} ثانية). متابعة؟",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ans != QMessageBox.Yes:
            return

        # Copy message once — stays in clipboard between openings
        copy_to_clipboard(msg)

        self._broadcast_queue = list(selected)
        self._broadcast_total = len(selected)
        self.progress.setMaximum(self._broadcast_total)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.progress_label.setVisible(True)
        self.progress_label.setText("بدء البث...")

        QTimer.singleShot(0, self._process_next_broadcast)

    def _process_next_broadcast(self):
        if not self._broadcast_queue:
            self.progress_label.setText(
                f"✅ تم فتح {self._broadcast_total} مجموعة. ألصق الرسالة (Ctrl+V) في كل واحدة."
            )
            QTimer.singleShot(3000, self._hide_progress)
            return

        g = self._broadcast_queue.pop(0)
        done = self._broadcast_total - len(self._broadcast_queue)
        self.progress.setValue(done)
        self.progress_label.setText(f"جارٍ الفتح: {g.name}")
        open_whatsapp_group(g.invite_link, "")  # clipboard already set

        QTimer.singleShot(BROADCAST_DELAY_MS, self._process_next_broadcast)

    def _hide_progress(self):
        self.progress.setVisible(False)
        self.progress_label.setVisible(False)

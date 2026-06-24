from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QWidget, QListWidget, QListWidgetItem, QLineEdit, QPlainTextEdit,
    QCheckBox, QSpinBox, QFormLayout, QMessageBox, QFileDialog, QApplication,
)

from app.services.backup_restore_service import (
    create_full_backup,
    stage_restore,
    validate_backup_archive,
)
from app.services.settings_service import (
    get_templates, save_templates, DEFAULT_TEMPLATES,
    get_notifications_enabled, set_notifications_enabled,
    get_notification_minutes, set_notification_minutes,
    get_last_full_backup, set_last_full_backup,
)
from app.ui.helpers.worker import run_in_background


TEMPLATE_VARS_HELP = (
    "المتغيرات المتاحة: "
    "{name} = اسم الطالب • "
    "{time} = وقت الحصة • "
    "{zoom} = رابط Zoom • "
    "{sessions} = عدد الحصص • "
    "{amount} = المبلغ المستحق"
)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("الإعدادات")
        self.resize(820, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        header = QLabel("الإعدادات")
        header.setObjectName("PageTitle")
        root.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_templates_tab(), "✉  قوالب الواتساب")
        tabs.addTab(self._build_notifications_tab(), "🔔  الإشعارات")
        tabs.addTab(self._build_backup_tab(), "🛡  النسخ والاستعادة")
        tabs.addTab(self._build_about_tab(), "ℹ  حول")
        root.addWidget(tabs, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = QPushButton("إغلاق")
        close_btn.setObjectName("GhostBtn")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)

    # ----------- TEMPLATES TAB -----------
    def _build_templates_tab(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)

        # Right side: list + buttons
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(QLabel("القوالب الحالية:"))
        self.template_list = QListWidget()
        self.template_list.currentRowChanged.connect(self._on_template_selected)
        right.addWidget(self.template_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("إضافة")
        add_btn.setObjectName("SuccessBtn")
        add_btn.clicked.connect(self._add_template)
        del_btn = QPushButton("حذف")
        del_btn.setObjectName("DangerBtn")
        del_btn.clicked.connect(self._delete_template)
        reset_btn = QPushButton("استعادة الافتراضية")
        reset_btn.setObjectName("GhostBtn")
        reset_btn.clicked.connect(self._reset_templates)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        right.addLayout(btn_row)

        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setMaximumWidth(280)
        layout.addWidget(right_widget)

        # Left side: editor
        editor = QVBoxLayout()
        editor.setSpacing(8)

        editor.addWidget(QLabel("اسم القالب:"))
        self.template_name = QLineEdit()
        self.template_name.textChanged.connect(self._on_template_edited)
        editor.addWidget(self.template_name)

        editor.addWidget(QLabel("نص الرسالة:"))
        self.template_text = QPlainTextEdit()
        self.template_text.textChanged.connect(self._on_template_edited)
        editor.addWidget(self.template_text, 1)

        help_label = QLabel(TEMPLATE_VARS_HELP)
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            "background-color: rgba(99,102,241,0.08); color: #4338CA;"
            "padding: 10px 14px; border-radius: 8px; font-size: 12px;"
        )
        editor.addWidget(help_label)

        save_btn = QPushButton("حفظ التعديلات")
        save_btn.setObjectName("SuccessBtn")
        save_btn.clicked.connect(self._save_templates)
        editor.addWidget(save_btn, alignment=Qt.AlignLeft)

        layout.addLayout(editor, 1)

        self._templates = get_templates()
        self._reload_template_list()
        self._current_template_idx = None
        self._loading_template = False
        return w

    def _reload_template_list(self):
        self.template_list.clear()
        for tpl in self._templates:
            QListWidgetItem(tpl.get("name", "قالب"), self.template_list)
        if self._templates:
            self.template_list.setCurrentRow(0)

    def _on_template_selected(self, row: int):
        if row < 0 or row >= len(self._templates):
            self._current_template_idx = None
            return
        self._current_template_idx = row
        self._loading_template = True
        tpl = self._templates[row]
        self.template_name.setText(tpl.get("name", ""))
        self.template_text.setPlainText(tpl.get("text", ""))
        self._loading_template = False

    def _on_template_edited(self):
        if self._loading_template or self._current_template_idx is None:
            return
        idx = self._current_template_idx
        self._templates[idx]["name"] = self.template_name.text()
        self._templates[idx]["text"] = self.template_text.toPlainText()
        item = self.template_list.item(idx)
        if item:
            item.setText(self.template_name.text() or "قالب")

    def _add_template(self):
        self._templates.append({"name": "قالب جديد", "text": "السلام عليكم {name}"})
        self._reload_template_list()
        self.template_list.setCurrentRow(len(self._templates) - 1)

    def _delete_template(self):
        if self._current_template_idx is None:
            return
        if QMessageBox.question(self, "حذف", "حذف هذا القالب؟") != QMessageBox.Yes:
            return
        del self._templates[self._current_template_idx]
        self._reload_template_list()

    def _reset_templates(self):
        if QMessageBox.question(
            self, "استعادة",
            "سيتم استبدال القوالب الحالية بالقوالب الافتراضية. متابعة؟"
        ) != QMessageBox.Yes:
            return
        self._templates = list(DEFAULT_TEMPLATES)
        self._reload_template_list()
        save_templates(self._templates)

    def _save_templates(self):
        save_templates(self._templates)
        QMessageBox.information(self, "تم", "تم حفظ القوالب.")

    # ----------- NOTIFICATIONS TAB -----------
    def _build_notifications_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(12)

        self.notif_enabled = QCheckBox("تفعيل إشعارات الحصص القادمة")
        self.notif_enabled.setChecked(get_notifications_enabled())
        self.notif_enabled.stateChanged.connect(
            lambda: set_notifications_enabled(self.notif_enabled.isChecked())
        )
        form.addRow("", self.notif_enabled)

        self.notif_minutes = QSpinBox()
        self.notif_minutes.setRange(1, 120)
        self.notif_minutes.setSuffix("  دقيقة")
        self.notif_minutes.setValue(get_notification_minutes())
        self.notif_minutes.valueChanged.connect(
            lambda v: set_notification_minutes(v)
        )
        form.addRow("التذكير قبل الحصة بـ:", self.notif_minutes)

        layout.addLayout(form)

        info = QLabel(
            "يظهر الإشعار عبر Tray Windows (أيقونة الساعة) قبل موعد الحصة بالفترة المحددة. "
            "تأكد أن التطبيق يعمل."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "background-color: rgba(16,185,129,0.08); color: #047857;"
            "padding: 12px 14px; border-radius: 10px;"
        )
        layout.addWidget(info)

        layout.addStretch(1)
        return w

    # ----------- BACKUP / RESTORE TAB -----------
    def _build_backup_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        title = QLabel("النسخ والاستعادة")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        intro = QLabel(
            "احفظ نسخة كاملة قابلة للنقل تشمل بياناتك والفواتير وملفات Excel، "
            "أو استعد نسخة سابقة بأمان."
        )
        intro.setWordWrap(True)
        intro.setObjectName("PageSubtitle")
        layout.addWidget(intro)

        self.create_backup_btn = QPushButton("إنشاء نسخة احتياطية كاملة")
        self.create_backup_btn.setObjectName("SuccessBtn")
        self.create_backup_btn.clicked.connect(self._choose_backup_destination)
        layout.addWidget(self.create_backup_btn)

        self.restore_backup_btn = QPushButton("استعادة نسخة احتياطية")
        self.restore_backup_btn.setObjectName("DangerBtn")
        self.restore_backup_btn.clicked.connect(self._choose_restore_archive)
        layout.addWidget(self.restore_backup_btn)

        latest_title = QLabel("آخر نسخة احتياطية كاملة")
        latest_title.setStyleSheet("font-weight: 700; margin-top: 12px;")
        layout.addWidget(latest_title)

        self.latest_backup_label = QLabel()
        self.latest_backup_label.setWordWrap(True)
        self.latest_backup_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.latest_backup_label.setStyleSheet(
            "background-color: rgba(99,102,241,0.08); color: #4338CA;"
            "padding: 12px 14px; border-radius: 10px;"
        )
        layout.addWidget(self.latest_backup_label)
        self._refresh_latest_backup()

        note = QLabel(
            "احتفظ بالنسخة على قرص خارجي أو مكان موثوق. النسخة تبقى محلية ولا "
            "تُرسل إلى أي خدمة تلقائياً."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748B; font-size: 12px;")
        layout.addWidget(note)
        layout.addStretch(1)
        return w

    def _refresh_latest_backup(self) -> None:
        latest = get_last_full_backup()
        if latest is None:
            self.latest_backup_label.setText("لم تُنشأ نسخة احتياطية كاملة بعد.")
            return
        created = latest["created_at"].replace("T", " ").replace("Z", "")
        self.latest_backup_label.setText(
            f"التاريخ: {created}\nالمكان: {latest['path']}"
        )

    def _choose_backup_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "اختر مكان حفظ النسخة الاحتياطية الكاملة"
        )
        if not selected:
            return
        self._set_portability_busy(True)
        run_in_background(
            self,
            lambda: create_full_backup(Path(selected)),
            on_result=self._on_full_backup_created,
            on_error=self._on_full_backup_error,
            on_finished=lambda: self._set_portability_busy(False),
        )

    def _on_full_backup_created(self, info) -> None:
        set_last_full_backup(str(info.archive_path), info.created_at)
        self._refresh_latest_backup()
        QMessageBox.information(
            self,
            "تم إنشاء النسخة",
            f"تم إنشاء النسخة الاحتياطية الكاملة بنجاح:\n{info.archive_path.name}",
        )

    def _on_full_backup_error(self, _error: Exception) -> None:
        QMessageBox.critical(
            self,
            "تعذر إنشاء النسخة",
            "لم نتمكن من إنشاء النسخة الاحتياطية الكاملة. تأكد من توفر مساحة "
            "كافية ومن إمكانية الكتابة في المكان المختار، ثم حاول مرة أخرى.",
        )

    def _choose_restore_archive(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "اختر نسخة Teacher Hub الاحتياطية",
            "",
            "Teacher Hub Backup (*.teacherhub.zip)",
        )
        if not selected:
            return
        archive_path = Path(selected)
        self._validated_restore = None
        self._set_portability_busy(True)
        run_in_background(
            self,
            lambda: validate_backup_archive(archive_path),
            on_result=lambda info: setattr(
                self, "_validated_restore", (archive_path, info)
            ),
            on_error=self._on_restore_error,
            on_finished=self._finish_restore_validation,
        )

    def _finish_restore_validation(self) -> None:
        self._set_portability_busy(False)
        validated = self._validated_restore
        self._validated_restore = None
        if validated is not None:
            self._confirm_restore(*validated)

    def _confirm_restore(self, archive_path: Path, info) -> None:
        created = info.created_at.replace("T", " ").replace("Z", "")
        message = (
            "تم التحقق من النسخة بنجاح.\n\n"
            f"تاريخ النسخة: {created}\n"
            f"إصدار البيانات: {info.schema_version}\n"
            f"عدد ملفات الفواتير وExcel: {info.export_count}\n"
            "حالة الفحص: سليمة\n\n"
            "سيتم أولاً إنشاء نسخة أمان كاملة من بياناتك الحالية. بعد ذلك سيُغلق "
            "التطبيق، وعند فتحه مرة أخرى ستكتمل الاستعادة قبل عرض بياناتك.\n\n"
            "هل تريد المتابعة؟"
        )
        if QMessageBox.question(
            self, "تأكيد استعادة النسخة", message
        ) != QMessageBox.Yes:
            return
        self._set_portability_busy(True)
        run_in_background(
            self,
            lambda: stage_restore(archive_path),
            on_result=self._on_restore_staged,
            on_error=self._on_restore_error,
            on_finished=lambda: self._set_portability_busy(False),
        )

    def _set_portability_busy(self, busy: bool) -> None:
        self.create_backup_btn.setEnabled(not busy)
        self.restore_backup_btn.setEnabled(not busy)

    def _on_restore_staged(self, _result) -> None:
        QMessageBox.information(
            self,
            "الاستعادة جاهزة",
            "تم تجهيز الاستعادة وإنشاء نسخة أمان من بياناتك الحالية. "
            "سيُغلق التطبيق الآن؛ افتحه مرة أخرى لإكمال الاستعادة بأمان.",
        )
        QTimer.singleShot(0, QApplication.quit)

    def _on_restore_error(self, _error: Exception) -> None:
        QMessageBox.critical(
            self,
            "تعذر تجهيز الاستعادة",
            "لم تتغير بياناتك الحالية. قد يكون ملف النسخة غير مكتمل أو غير صالح؛ "
            "اختر نسخة Teacher Hub أخرى وحاول مرة جديدة.",
        )

    # ----------- ABOUT TAB -----------
    def _build_about_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(10)
        layout.addStretch(1)

        icon_lbl = QLabel("📚")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 56px;")
        layout.addWidget(icon_lbl)

        title = QLabel("Teacher Hub")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 26px; font-weight: 700;")
        layout.addWidget(title)

        ver = QLabel("الإصدار 2.0")
        ver.setAlignment(Qt.AlignCenter)
        ver.setObjectName("StudentMeta")
        layout.addWidget(ver)

        desc = QLabel(
            "تطبيق إدارة متكامل لمعلمي الأونلاين.\n"
            "جدول حصص، تتبع دفعات، فواتير PDF، وتذكيرات."
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #64748B; padding: 14px;")
        layout.addWidget(desc)

        layout.addStretch(2)
        return w

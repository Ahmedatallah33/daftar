from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QMessageBox, QComboBox,
    QFrame
)

from app.config import CURRENCY
from app.services.billing_service import (
    list_invoices, mark_invoice_paid, delete_invoice
)
from app.services.launcher import open_path
from app.ui.helpers.icons import icon, ICONS
from app.ui.helpers.theme import theme_manager
from app.ui.pages.billing_page import InvoicePostIssueDialog


class InvoicesPage(QWidget):
    data_changed = Signal()

    def __init__(self, auto_refresh: bool = True):
        super().__init__()
        self.setObjectName("CentralSurface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title = QLabel("سجل الفواتير")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)

        self.filter_combo = QComboBox()
        self.filter_combo.addItem("الكل", None)
        self.filter_combo.addItem("غير مدفوعة", False)
        self.filter_combo.addItem("مدفوعة", True)
        self.filter_combo.setFixedWidth(160)
        self.filter_combo.currentIndexChanged.connect(self.refresh)
        title_row.addWidget(self.filter_combo)

        layout.addLayout(title_row)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        layout.addWidget(self.subtitle)

        layout.addSpacing(10)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        self.total_card = self._stat_card("الإجمالي", "0", "#6366F1")
        self.paid_card = self._stat_card("المحصّل", "0", "#10B981")
        self.pending_card = self._stat_card("المتبقي", "0", "#F59E0B")
        stats_row.addWidget(self.total_card["frame"])
        stats_row.addWidget(self.paid_card["frame"])
        stats_row.addWidget(self.pending_card["frame"])
        stats_row.addStretch(1)
        layout.addLayout(stats_row)

        layout.addSpacing(14)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "التاريخ", "الطالب", "الحصص", "فيديوهات",
            "المبلغ", "الحالة", "إجراءات"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(54)
        layout.addWidget(self.table, 1)

        if auto_refresh:
            self.refresh()

    def _stat_card(self, title: str, value: str, color: str) -> dict:
        frame = QFrame()
        frame.setObjectName("StatCard")
        frame.setFixedHeight(90)
        v = QVBoxLayout(frame)
        v.setContentsMargins(18, 14, 18, 14)
        lbl = QLabel(title)
        lbl.setObjectName("StudentMeta")
        val = QLabel(value)
        val.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: 700;")
        v.addWidget(lbl)
        v.addWidget(val)
        return {"frame": frame, "label": val, "color": color}

    def _set_stat(self, card: dict, text: str):
        card["label"].setText(text)

    def refresh(self):
        filter_val = self.filter_combo.currentData()
        invoices = list_invoices(only_paid=filter_val)

        all_invoices = list_invoices(only_paid=None)
        total = sum(i.amount for i in all_invoices)
        paid = sum(i.amount for i in all_invoices if i.is_paid)
        pending = total - paid

        self._set_stat(self.total_card, f"{total:.0f} {CURRENCY}")
        self._set_stat(self.paid_card, f"{paid:.0f} {CURRENCY}")
        self._set_stat(self.pending_card, f"{pending:.0f} {CURRENCY}")

        self.subtitle.setText(f"عدد الفواتير المعروضة: {len(invoices)} من إجمالي {len(all_invoices)}")

        self.table.setRowCount(len(invoices))
        for row, inv in enumerate(invoices):
            student_name = inv.student.name if inv.student else "—"
            self.table.setItem(row, 0, QTableWidgetItem(inv.issued_at.strftime("%Y-%m-%d")))
            self.table.setItem(row, 1, QTableWidgetItem(student_name))
            self.table.setItem(row, 2, QTableWidgetItem(str(inv.sessions_count)))
            self.table.setItem(row, 3, QTableWidgetItem(str(inv.videos_count)))
            self.table.setItem(row, 4, QTableWidgetItem(f"{inv.amount:.2f} {CURRENCY}"))

            status_widget = QLabel("مدفوعة" if inv.is_paid else "غير مدفوعة")
            status_widget.setObjectName("BadgePaid" if inv.is_paid else "BadgePending")
            status_widget.setAlignment(Qt.AlignCenter)
            wrapper = QWidget()
            wl = QHBoxLayout(wrapper)
            wl.setContentsMargins(6, 4, 6, 4)
            wl.addWidget(status_widget)
            self.table.setCellWidget(row, 5, wrapper)

            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(6, 0, 6, 0)
            al.setSpacing(4)

            open_btn = QPushButton()
            open_btn.setObjectName("LinkBtn")
            open_btn.setIcon(icon(ICONS["open"], color=theme_manager.accent_color()))
            open_btn.setIconSize(QSize(14, 14))
            open_btn.setToolTip("فتح PDF")
            open_btn.setFixedSize(32, 32)
            open_btn.clicked.connect(lambda _=False, p=inv.pdf_path: self._open(p))
            al.addWidget(open_btn)

            wa_btn = QPushButton()
            wa_btn.setObjectName("LinkBtn")
            wa_btn.setIcon(icon(ICONS["whatsapp"], color="#25D366"))
            wa_btn.setIconSize(QSize(14, 14))
            wa_btn.setToolTip("إرسال الفاتورة عبر واتساب")
            wa_btn.setFixedSize(32, 32)
            wa_btn.clicked.connect(lambda _=False, i=inv.id: self._send_wa(i))
            al.addWidget(wa_btn)

            toggle_btn = QPushButton()
            toggle_btn.setObjectName("LinkBtn")
            toggle_btn.setIcon(icon(
                ICONS["x"] if inv.is_paid else ICONS["check"],
                color="#EF4444" if inv.is_paid else "#10B981"
            ))
            toggle_btn.setIconSize(QSize(14, 14))
            toggle_btn.setToolTip("تبديل حالة الدفع")
            toggle_btn.setFixedSize(32, 32)
            toggle_btn.clicked.connect(lambda _=False, i=inv.id, p=inv.is_paid: self._toggle(i, not p))
            al.addWidget(toggle_btn)

            del_btn = QPushButton()
            del_btn.setObjectName("LinkBtn")
            del_btn.setIcon(icon(ICONS["delete"], color="#EF4444"))
            del_btn.setIconSize(QSize(14, 14))
            del_btn.setToolTip("حذف الفاتورة")
            del_btn.setFixedSize(32, 32)
            del_btn.clicked.connect(lambda _=False, i=inv.id: self._delete(i))
            al.addWidget(del_btn)

            al.addStretch(1)
            self.table.setCellWidget(row, 6, actions)

    def _open(self, pdf_path: str):
        if not pdf_path or not Path(pdf_path).exists():
            QMessageBox.warning(self, "خطأ", "ملف PDF غير موجود.")
            return
        open_path(pdf_path)

    def _send_wa(self, invoice_id: int):
        # Find the invoice again
        all_invoices = list_invoices(only_paid=None)
        inv = next((i for i in all_invoices if i.id == invoice_id), None)
        if inv is None or inv.student is None:
            QMessageBox.warning(self, "خطأ", "لم يتم العثور على الفاتورة أو الطالب.")
            return
        pdf_path = Path(inv.pdf_path) if inv.pdf_path else None
        if pdf_path is None or not pdf_path.exists():
            QMessageBox.warning(
                self, "ملف مفقود",
                "ملف الـ PDF الأصلي لم يعد موجوداً. يمكنك إرسال الرسالة النصية فقط."
            )
            # Still allow sending message without pdf; use a placeholder path
            pdf_path = Path(inv.pdf_path or "invoice.pdf")

        dlg = InvoicePostIssueDialog(
            student=inv.student,
            data={
                "sessions": inv.sessions_count,
                "videos": inv.videos_count,
                "amount": inv.amount,
            },
            pdf_path=pdf_path,
            parent=self,
        )
        dlg.exec()

    def _toggle(self, invoice_id: int, paid: bool):
        mark_invoice_paid(invoice_id, paid)
        self.data_changed.emit()

    def _delete(self, invoice_id: int):
        if QMessageBox.question(
            self, "حذف",
            "سيتم حذف الفاتورة من السجل فقط (الملف لن يُحذف). متابعة؟"
        ) != QMessageBox.Yes:
            return
        delete_invoice(invoice_id)
        self.data_changed.emit()

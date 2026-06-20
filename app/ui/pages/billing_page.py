from datetime import datetime
import subprocess
import sys

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QPushButton, QMessageBox, QInputDialog, QDialog, QTextEdit
)

from app.config import CURRENCY
from app.services.billing_service import (
    students_with_dues, reset_cycle, record_invoice
)
from app.services.pdf_service import generate_invoice
from app.services.launcher import open_path, open_whatsapp, render_template
from app.services.settings_service import get_invoice_message_template
from app.ui.helpers.icons import icon, ICONS
from app.ui.helpers.shadow import add_shadow
from app.ui.helpers.theme import theme_manager
from app.ui.helpers.worker import run_in_background


class DueCard(QFrame):
    def __init__(self, data: dict, parent_page):
        super().__init__()
        self.data = data
        self.parent_page = parent_page
        self.last_invoice_path = None
        self.last_invoice_id = None
        self.setObjectName("Card")
        self.setProperty("status", "due")
        add_shadow(self, blur=20, y_offset=4, opacity=24)
        self._build()

    def _build(self):
        student = self.data["student"]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(12)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        name = QLabel(student.name)
        name.setObjectName("StudentName")
        name_col.addWidget(name)
        sub = QLabel(
            f"<span style='color:#64748B'>عدد الحصص: </span><b>{self.data['sessions']}</b>"
            f"  •  <span style='color:#64748B'>فيديوهات: </span><b>{self.data['videos']}</b>"
            f"  •  <span style='color:#64748B'>سعر الحصة: </span><b>{student.price_per_session:.2f} {CURRENCY}</b>"
        )
        sub.setObjectName("StudentMeta")
        name_col.addWidget(sub)
        top.addLayout(name_col, 1)

        amount_label = QLabel(f"{self.data['amount']:.0f} {CURRENCY}")
        amount_label.setObjectName("AmountBig")
        top.addWidget(amount_label, 0, Qt.AlignTop)

        layout.addLayout(top)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        pdf_btn = QPushButton("  إصدار فاتورة PDF")
        pdf_btn.setIcon(icon(ICONS["pdf"], color="#FFFFFF"))
        pdf_btn.setIconSize(QSize(16, 16))
        pdf_btn.clicked.connect(self._issue_pdf)
        actions.addWidget(pdf_btn)
        self.pdf_btn = pdf_btn

        reset_btn = QPushButton("  تصفير العداد (تم التحصيل)")
        reset_btn.setObjectName("SuccessBtn")
        reset_btn.setIcon(icon(ICONS["check"], color="#FFFFFF"))
        reset_btn.setIconSize(QSize(16, 16))
        reset_btn.clicked.connect(self._reset)
        actions.addWidget(reset_btn)

        actions.addStretch(1)
        layout.addLayout(actions)

    def _issue_pdf(self):
        notes, ok = QInputDialog.getMultiLineText(
            self, "ملاحظات الفاتورة",
            "اكتب ملاحظات اختيارية تظهر في الفاتورة:", ""
        )
        if not ok:
            return
        student_id = self.data["student"].id
        amount = self.data["amount"]
        sessions_count = self.data["sessions"]
        videos_count = self.data["videos"]

        def generate_and_record():
            from app.services.student_service import get_student

            student = get_student(student_id)
            if student is None:
                raise ValueError("Student no longer exists")
            path = generate_invoice(student, amount, notes)
            inv = record_invoice(
                student_id=student_id,
                sessions_count=sessions_count,
                videos_count=videos_count,
                amount=amount,
                pdf_path=str(path),
                notes=notes,
                is_paid=False,
            )
            return path, inv.id

        self.pdf_btn.setEnabled(False)
        run_in_background(
            self,
            generate_and_record,
            on_result=self._on_invoice_ready,
            on_error=self._on_invoice_error,
            on_finished=lambda: self.pdf_btn.setEnabled(True),
        )

    def _on_invoice_ready(self, result):
        path, invoice_id = result
        self.last_invoice_path = path
        self.last_invoice_id = invoice_id

        # Post-generation: offer to send over WhatsApp + open PDF
        dlg = InvoicePostIssueDialog(
            student=self.data["student"],
            data=self.data,
            pdf_path=path,
            parent=self,
        )
        dlg.exec()
        self.parent_page.data_changed.emit()

    def _on_invoice_error(self, error):
        QMessageBox.critical(self, "خطأ في توليد الفاتورة", str(error))

    def _reset(self):
        if QMessageBox.question(
            self, "تأكيد التصفير",
            "سيتم تصفير عداد الطالب وأرشفة الحصص الحالية. "
            "تأكد من تحصيل المبلغ أولاً.\n\nهل ترغب بالمتابعة؟"
        ) != QMessageBox.Yes:
            return
        reset_cycle(self.data["student"].id)
        if self.last_invoice_id is not None:
            from app.services.billing_service import mark_invoice_paid
            mark_invoice_paid(self.last_invoice_id, True)
        self.parent_page.data_changed.emit()


class BillingPage(QWidget):
    data_changed = Signal()

    def __init__(self, auto_refresh: bool = True):
        super().__init__()
        self.setObjectName("CentralSurface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(6)

        title = QLabel("المستحقات والفواتير")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        layout.addWidget(self.subtitle)

        layout.addSpacing(12)

        self.total_badge = QLabel()
        self.total_badge.setObjectName("AmountBig")
        self.total_badge.setAlignment(Qt.AlignCenter)
        self.total_badge.setMaximumWidth(420)
        layout.addWidget(self.total_badge)

        layout.addSpacing(10)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }")
        layout.addWidget(self.scroll, 1)

        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(14)
        self.container_layout.addStretch(1)
        self.scroll.setWidget(self.container)

        if auto_refresh:
            self.refresh()

    def refresh(self):
        while self.container_layout.count() > 0:
            item = self.container_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        dues = students_with_dues()
        total = sum(d["amount"] for d in dues)

        if not dues:
            self.subtitle.setText("لا توجد مستحقات حالياً.")
            self.total_badge.setText(f"الإجمالي: 0 {CURRENCY}")
            empty = QWidget()
            ev = QVBoxLayout(empty)
            ev.setAlignment(Qt.AlignCenter)
            icon_lbl = QLabel("🎉")
            icon_lbl.setObjectName("EmptyStateIcon")
            icon_lbl.setAlignment(Qt.AlignCenter)
            text_lbl = QLabel("كل الطلاب ضمن دوراتهم. لا شيء للتحصيل الآن.")
            text_lbl.setObjectName("EmptyState")
            text_lbl.setAlignment(Qt.AlignCenter)
            ev.addStretch(1)
            ev.addWidget(icon_lbl)
            ev.addWidget(text_lbl)
            ev.addStretch(2)
            self.container_layout.insertWidget(0, empty)
            return

        self.subtitle.setText(f"{len(dues)} طالباً تجاوزوا الحد المطلوب.")
        self.total_badge.setText(f"💰  إجمالي المستحقات: {total:.0f} {CURRENCY}")

        for d in dues:
            card = DueCard(d, self)
            self.container_layout.insertWidget(self.container_layout.count() - 1, card)


# ---------------------------------------------------------------------------
# InvoicePostIssueDialog — shown right after a PDF invoice is generated.
# Offers: copy/edit message → send via WhatsApp, open PDF, open folder.
# ---------------------------------------------------------------------------

class InvoicePostIssueDialog(QDialog):
    def __init__(self, student, data, pdf_path, parent=None):
        super().__init__(parent)
        self.student = student
        self.data = data
        self.pdf_path = pdf_path
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("تم إصدار الفاتورة — إرسالها للطالب")
        self.setMinimumWidth(620)
        self._build()

    def _build(self):
        is_dark = theme_manager.is_dark()
        card_bg = theme_manager.card_bg()
        text_col = theme_manager.text_color()
        muted_col = theme_manager.muted_text_color()
        divider = theme_manager.divider_color()
        border_col = theme_manager.input_border()
        hover_bg = "#273449" if is_dark else "#F1F5F9"
        input_bg = "#172033" if is_dark else "#FAFAFA"
        hint_bg = "#78350F" if is_dark else "#FEF3C7"
        hint_fg = "#FDE68A" if is_dark else "#92400E"

        # Paint the dialog surface so non-widget gaps don't show through as white
        self.setStyleSheet(f"QDialog {{ background: {card_bg}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header — green success banner
        header = QFrame()
        header.setStyleSheet(
            "background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #10B981, stop:1 #059669);"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(22, 16, 22, 16)
        hl.setSpacing(12)

        ic = QLabel()
        ic.setPixmap(icon(ICONS["check"], color="#FFFFFF").pixmap(28, 28))
        ic.setStyleSheet("background: transparent;")
        hl.addWidget(ic)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        t = QLabel("تم إصدار الفاتورة بنجاح")
        t.setStyleSheet("color: white; font-size: 17px; font-weight: 700;")
        sub = QLabel(f"{self.pdf_path.name}")
        sub.setStyleSheet("color: rgba(255,255,255,0.9); font-size: 12px;")
        titles.addWidget(t)
        titles.addWidget(sub)
        hl.addLayout(titles, 1)
        root.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(22, 18, 22, 14)
        body.setSpacing(12)

        # Student summary line
        phone_display = self.student.phone or "— لا يوجد رقم"
        info = QLabel(
            f"<span style='color:{muted_col}'>الطالب:</span> <b>{self.student.name}</b>"
            f"   •   <span style='color:{muted_col}'>الواتساب:</span> <b>{phone_display}</b>"
            f"   •   <span style='color:{muted_col}'>المبلغ:</span> "
            f"<b style='color:#10B981'>{self.data['amount']:.0f} {CURRENCY}</b>"
        )
        info.setStyleSheet(f"font-size: 13px; color: {text_col};")
        body.addWidget(info)

        # Editable message
        tip = QLabel("ستُرسل الرسالة التالية عبر واتساب (يمكن تعديلها):")
        tip.setStyleSheet(f"color: {muted_col}; font-size: 12px; font-weight: 600;")
        body.addWidget(tip)

        tpl = get_invoice_message_template()
        rendered = render_template(
            tpl,
            name=self.student.name,
            sessions=self.data["sessions"],
            videos=self.data["videos"],
            amount=f"{self.data['amount']:.0f} {CURRENCY}",
            date=datetime.now().strftime("%Y-%m-%d"),
            time=self.student.session_time or "",
            zoom=self.student.zoom_link or "",
        )
        self.message_edit = QTextEdit()
        self.message_edit.setPlainText(rendered)
        self.message_edit.setMinimumHeight(160)
        self.message_edit.setStyleSheet(
            f"QTextEdit {{ background:{input_bg}; color:{text_col}; "
            f"  border:1.5px solid {border_col};"
            "  border-radius: 10px; padding: 10px 12px; font-size: 13px; "
            "  line-height: 1.6; }"
            "QTextEdit:focus { border: 2px solid #10B981; padding: 9px 11px; }"
        )
        body.addWidget(self.message_edit)

        # Hint about attaching PDF
        hint = QLabel(
            "💡 بعد فتح واتساب، اسحب ملف الـ PDF من المجلد وأفلته داخل المحادثة لإرفاقه. "
            "استخدم الزر «فتح مجلد الفاتورة» لفتح المجلد بسرعة."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"background:{hint_bg}; color:{hint_fg}; padding: 10px 12px; "
            "border-radius: 8px; font-size: 12px; font-weight: 500;"
        )
        body.addWidget(hint)

        root.addLayout(body)

        # Footer actions
        footer = QFrame()
        footer.setStyleSheet(
            f"background:{card_bg}; border-top: 1px solid {divider};"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(22, 14, 22, 14)
        fl.setSpacing(10)

        pdf_btn_bg = "#1E1B4B" if is_dark else "#EEF2FF"
        pdf_btn_fg = "#C7D2FE" if is_dark else "#4338CA"
        pdf_btn_border = "#3730A3" if is_dark else "#C7D2FE"
        pdf_btn_hover = "#312E81" if is_dark else "#E0E7FF"

        open_pdf_btn = QPushButton("  فتح PDF")
        open_pdf_btn.setIcon(icon(ICONS["pdf"], color=pdf_btn_fg))
        open_pdf_btn.setIconSize(QSize(15, 15))
        open_pdf_btn.setMinimumSize(130, 42)
        open_pdf_btn.setCursor(Qt.PointingHandCursor)
        open_pdf_btn.setStyleSheet(
            f"QPushButton {{ background:{pdf_btn_bg}; color:{pdf_btn_fg}; "
            f"border: 1.5px solid {pdf_btn_border}; border-radius: 10px; "
            "padding: 0 16px; font-size: 13px; font-weight: 600; }"
            f"QPushButton:hover {{ background:{pdf_btn_hover}; }}"
        )
        open_pdf_btn.clicked.connect(self._open_pdf)
        fl.addWidget(open_pdf_btn)

        open_folder_btn = QPushButton("  فتح مجلد الفاتورة")
        open_folder_btn.setIcon(icon(ICONS["open"], color=muted_col))
        open_folder_btn.setIconSize(QSize(15, 15))
        open_folder_btn.setMinimumSize(170, 42)
        open_folder_btn.setCursor(Qt.PointingHandCursor)
        open_folder_btn.setStyleSheet(
            f"QPushButton {{ background:{card_bg}; color:{muted_col}; "
            f"border: 1.5px solid {border_col}; border-radius: 10px; "
            "padding: 0 16px; font-size: 13px; font-weight: 600; }"
            f"QPushButton:hover {{ background:{hover_bg}; color:{text_col}; }}"
        )
        open_folder_btn.clicked.connect(self._open_folder)
        fl.addWidget(open_folder_btn)

        fl.addStretch(1)

        close_btn = QPushButton("إغلاق")
        close_btn.setMinimumSize(110, 42)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ background:{card_bg}; color:{muted_col}; "
            f"border: 1.5px solid {border_col}; border-radius: 10px; "
            "padding: 0 18px; font-size: 13px; font-weight: 600; }"
            f"QPushButton:hover {{ background:{hover_bg}; color:{text_col}; }}"
        )
        close_btn.clicked.connect(self.accept)
        fl.addWidget(close_btn)

        wa_style = (
            "QPushButton { background:#25D366; color:#FFFFFF; "
            "border: none; border-radius: 10px; "
            "padding: 0 18px; font-size: 13px; font-weight: 700; }"
            "QPushButton:hover { background:#16A34A; }"
            "QPushButton:pressed { background:#15803D; }"
            "QPushButton:disabled { background:#9CA3AF; }"
        )

        # Send to guardian (parent_phone)
        guardian_phone = (getattr(self.student, "parent_phone", "") or "").strip()
        send_guardian_btn = QPushButton("  إرسال إلى ولي الأمر")
        send_guardian_btn.setIcon(icon(ICONS["whatsapp"], color="#FFFFFF"))
        send_guardian_btn.setIconSize(QSize(15, 15))
        send_guardian_btn.setMinimumSize(220, 42)
        send_guardian_btn.setCursor(Qt.PointingHandCursor)
        send_guardian_btn.setStyleSheet(wa_style)
        if not guardian_phone:
            send_guardian_btn.setEnabled(False)
            send_guardian_btn.setToolTip("لا يوجد رقم ولي أمر مسجّل لهذا الطالب")
        else:
            send_guardian_btn.setToolTip(f"إرسال إلى ولي الأمر: {guardian_phone}")
        send_guardian_btn.clicked.connect(self._send_to_guardian)
        fl.addWidget(send_guardian_btn)

        send_btn = QPushButton("  إرسال إلى الطالب")
        send_btn.setIcon(icon(ICONS["whatsapp"], color="#FFFFFF"))
        send_btn.setIconSize(QSize(16, 16))
        send_btn.setMinimumSize(200, 42)
        send_btn.setDefault(True)
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.setStyleSheet(wa_style)
        if not self.student.phone:
            send_btn.setEnabled(False)
            send_btn.setToolTip("لا يوجد رقم واتساب مسجّل لهذا الطالب")
        send_btn.clicked.connect(self._send_whatsapp)
        fl.addWidget(send_btn)

        root.addWidget(footer)

    def _open_pdf(self):
        open_path(self.pdf_path)

    def _open_folder(self):
        folder = self.pdf_path.parent
        try:
            if sys.platform.startswith("win"):
                # /select, highlights the file in Explorer
                subprocess.Popen(["explorer", "/select,", str(self.pdf_path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(self.pdf_path)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            QMessageBox.warning(self, "خطأ", f"تعذر فتح المجلد: {e}")

    def _send_whatsapp(self):
        self._send_via_whatsapp(self.student.phone, "الطالب")

    def _send_to_guardian(self):
        phone = (getattr(self.student, "parent_phone", "") or "").strip()
        self._send_via_whatsapp(phone, "ولي الأمر")

    def _send_via_whatsapp(self, phone: str, who: str):
        if not phone:
            QMessageBox.warning(
                self, "لا يوجد رقم",
                f"لا يوجد رقم واتساب مسجّل لـ{who}."
            )
            return
        message = self.message_edit.toPlainText().strip()
        # WhatsApp URL schemes don't support file attachments — open chat with
        # summary text + open the folder so user can drag the PDF into chat.
        if not open_whatsapp(phone, message):
            QMessageBox.warning(
                self, "تعذر فتح واتساب",
                "تأكد من تثبيت WhatsApp Desktop أو اتصال الإنترنت لفتح WhatsApp Web."
            )
            return
        self._open_folder()
        QMessageBox.information(
            self, "خطوة أخيرة",
            f"تم فتح محادثة {who} مع نص الفاتورة.\n"
            "اسحب ملف PDF من المجلد إلى المحادثة لإرفاق الفاتورة."
        )
        self.accept()

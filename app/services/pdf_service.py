from datetime import datetime
from pathlib import Path

import os
from pathlib import Path as _Path

import arabic_reshaper
from arabic_reshaper import ArabicReshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)

from app.config import FONTS_DIR, INVOICES_DIR, TEACHER_NAME, CURRENCY
from app.db.models import Student
from app.services.session_service import list_sessions, list_videos

_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

# Reshaper without ligatures — Cairo TTF lacks some ligature glyphs and
# enabling them produces missing letters. Standard letter shaping is enough.
_RESHAPER = ArabicReshaper(configuration={
    "delete_harakat": False,
    "support_ligatures": False,
})


def _windows_fonts_dir() -> _Path:
    return _Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def _register_font() -> tuple[str, str]:
    """Register a font with full Arabic glyph coverage. Returns (regular, bold).

    Priority: Windows system fonts (Tahoma/Arial — full Arabic coverage),
    then bundled Cairo (subsetted, may lack glyphs), then Helvetica fallback.
    """
    global _FONT_REGISTERED, _FONT_NAME, _FONT_BOLD
    if _FONT_REGISTERED:
        return _FONT_NAME, _FONT_BOLD

    win_fonts = _windows_fonts_dir()

    # (family, regular_path, bold_path)
    candidates = [
        ("Tahoma", win_fonts / "tahoma.ttf", win_fonts / "tahomabd.ttf"),
        ("Arial", win_fonts / "arial.ttf", win_fonts / "arialbd.ttf"),
        ("Cairo", FONTS_DIR / "Cairo-Regular.ttf", FONTS_DIR / "Cairo-Bold.ttf"),
        ("Tajawal", FONTS_DIR / "Tajawal-Regular.ttf", FONTS_DIR / "Tajawal-Bold.ttf"),
        ("Amiri", FONTS_DIR / "Amiri-Regular.ttf", FONTS_DIR / "Amiri-Bold.ttf"),
    ]
    for family, reg_path, bold_path in candidates:
        if not reg_path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(family, str(reg_path)))
        except Exception:
            continue

        bold_family = family
        if bold_path.exists():
            bold_family = f"{family}-Bold"
            try:
                pdfmetrics.registerFont(TTFont(bold_family, str(bold_path)))
            except Exception:
                bold_family = family

        try:
            registerFontFamily(family, normal=family, bold=bold_family,
                               italic=family, boldItalic=bold_family)
        except Exception:
            pass

        _FONT_NAME = family
        _FONT_BOLD = bold_family
        _FONT_REGISTERED = True
        return _FONT_NAME, _FONT_BOLD
    return _FONT_NAME, _FONT_BOLD


def ar(text: str) -> str:
    """Reshape Arabic text with proper letter shaping + RTL bidi for ReportLab."""
    if text is None or text == "":
        return ""
    s = str(text)
    try:
        reshaped = _RESHAPER.reshape(s)
        return get_display(reshaped, base_dir="R")
    except Exception:
        # Fallback to default reshaper if custom one fails
        return get_display(arabic_reshaper.reshape(s), base_dir="R")


def _safe_filename(name: str) -> str:
    keep = "-_ "
    cleaned = "".join(c for c in name if c.isalnum() or c in keep or ord(c) > 127)
    return cleaned.strip() or "student"


def generate_invoice(student: Student, amount: float, notes: str = "") -> Path:
    font, font_bold = _register_font()
    sessions = [s for s in list_sessions(student.id) if s.counted]
    videos = [v for v in list_videos(student.id) if v.counted]

    today = datetime.now()
    filename = f"{_safe_filename(student.name)}_{today.strftime('%Y%m%d_%H%M%S')}.pdf"
    out_path = INVOICES_DIR / filename

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Invoice - {student.name}",
    )

    title_style = ParagraphStyle(
        "title", fontName=font_bold, fontSize=22, alignment=1,
        textColor=colors.HexColor("#1F3A8A"), leading=28,
    )
    h2_style = ParagraphStyle(
        "h2", fontName=font_bold, fontSize=14, alignment=2, leading=20,
        textColor=colors.HexColor("#111827"),
    )
    body_style = ParagraphStyle(
        "body", fontName=font, fontSize=12, alignment=2, leading=18,
    )
    small_style = ParagraphStyle(
        "small", fontName=font, fontSize=10, alignment=2, leading=14,
        textColor=colors.HexColor("#6B7280"),
    )

    story = []
    story.append(Paragraph(ar(f"فاتورة حصص — {TEACHER_NAME}"), title_style))
    story.append(Spacer(1, 4 * mm))
    invoice_no = today.strftime('%Y%m%d-%H%M%S')
    story.append(Paragraph(
        ar(f"رقم الفاتورة: {invoice_no}   •   تاريخ الإصدار: {today.strftime('%Y-%m-%d %H:%M')}"),
        small_style,
    ))
    story.append(Spacer(1, 6 * mm))

    info_data = [
        [ar(student.name), ar("اسم الطالب:")],
        [ar(str(len(sessions))), ar("عدد الحصص:")],
        [ar(str(len(videos))), ar("عدد الفيديوهات المرسلة:")],
        [ar(f"{student.price_per_session:.2f} {CURRENCY}"), ar("سعر الحصة:")],
        [ar(f"{amount:.2f} {CURRENCY}"), ar("المبلغ المستحق:")],
    ]
    info_table = Table(info_data, colWidths=[110 * mm, 60 * mm])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), font),
        ("FONTNAME", (1, 0), (1, -1), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), 12),
        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#F3F4F6")),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#1F2937")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph(ar("سجل الحصص:"), h2_style))
    story.append(Spacer(1, 3 * mm))

    session_rows = [[ar("ملاحظات"), ar("الوقت"), ar("التاريخ"), ar("#")]]
    for idx, s in enumerate(reversed(sessions), start=1):
        session_rows.append([
            ar(s.notes or "-"),
            ar(s.session_date.strftime("%H:%M")),
            ar(s.session_date.strftime("%Y-%m-%d")),
            ar(str(idx)),
        ])
    sessions_table = Table(session_rows, colWidths=[60 * mm, 30 * mm, 50 * mm, 20 * mm])
    sessions_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A8A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D1D5DB")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(sessions_table)
    story.append(Spacer(1, 8 * mm))

    if videos:
        story.append(Paragraph(ar("الفيديوهات المرسلة:"), h2_style))
        story.append(Spacer(1, 3 * mm))
        video_rows = [[ar("التاريخ"), ar("الوصف"), ar("#")]]
        for idx, v in enumerate(reversed(videos), start=1):
            video_rows.append([
                ar(v.sent_date.strftime("%Y-%m-%d")),
                ar(v.description or "-"),
                ar(str(idx)),
            ])
        videos_table = Table(video_rows, colWidths=[50 * mm, 90 * mm, 20 * mm])
        videos_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTNAME", (0, 0), (-1, 0), font_bold),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#059669")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D1D5DB")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0FDF4")]),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(videos_table)
        story.append(Spacer(1, 8 * mm))

    if notes:
        story.append(Paragraph(ar("ملاحظات المعلم:"), h2_style))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(ar(notes), body_style))
        story.append(Spacer(1, 6 * mm))

    total_data = [[
        ar(f"{amount:.2f} {CURRENCY}"),
        ar("الإجمالي المستحق:"),
    ]]
    total_table = Table(total_data, colWidths=[110 * mm, 60 * mm])
    total_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), 16),
        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEF3C7")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#92400E")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#F59E0B")),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(total_table)
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(ar(f"شكراً لكم - {TEACHER_NAME}"), small_style))

    doc.build(story)
    return out_path

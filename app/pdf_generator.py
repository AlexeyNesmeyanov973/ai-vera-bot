# app/pdf_generator.py
import logging
from datetime import datetime
from typing import List
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

logger = logging.getLogger(__name__)


def _split_into_paragraphs(text: str) -> List[str]:
    # Нормализуем двойные переносы, режем «огромные» абзацы на части
    raw = (text or "").strip().replace("\r\n", "\n")
    blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
    out: List[str] = []
    for b in blocks:
        # если абзац слишком длинный — порежем по ~1500 символов
        if len(b) <= 1500:
            out.append(b)
        else:
            s = 0
            step = 1500
            while s < len(b):
                out.append(b[s : s + step])
                s += step
    return out


class PDFGenerator:
    """Генерация PDF с кириллицей, номером страниц и аккуратным форматированием."""

    def __init__(self):
        self.styles = getSampleStyleSheet()
        self._setup_fonts()

    def _setup_fonts(self):
        # Пытаемся использовать DejaVu Sans; если не найден — останемся на встроенных
        try:
            pdfmetrics.registerFont(TTFont("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
            self.styles["Normal"].fontName = "DejaVuSans"
            self.styles["Title"].fontName = "DejaVuSans-Bold"
            self.styles["Heading1"].fontName = "DejaVuSans-Bold"
        except Exception as e:
            logger.warning("Не удалось настроить шрифты DejaVuSans: %s", e)

    @staticmethod
    def _footer(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFont("DejaVuSans" if "DejaVuSans" in pdfmetrics.getRegisteredFontNames() else "Helvetica", 9)
        canvas.setFillColor(colors.grey)
        canvas.drawString(15 * mm, 10 * mm, "AI-Vera • Transcription")
        canvas.drawRightString(w - 15 * mm, 10 * mm, f"Стр. {doc.page}")
        canvas.restoreState()

    def generate_transcription_pdf(self, text: str, output_path: str, title: str = "Транскрибация") -> bool:
        try:
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=15 * mm,
                leftMargin=15 * mm,
                topMargin=18 * mm,
                bottomMargin=18 * mm,
                title=title,
                author="AI-Vera",
                subject="Transcription",
                creator="AI-Vera",
            )

            story: List = []
            title_style = self.styles["Title"]
            title_style.fontSize = 18
            title_style.leading = 22

            story.append(Paragraph(escape(title), title_style))
            story.append(Spacer(1, 8))

            meta_style = ParagraphStyle(
                "Meta",
                parent=self.styles["Normal"],
                fontSize=9,
                textColor=colors.HexColor("#666666"),
            )
            date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
            story.append(Paragraph(escape(f"Сгенерировано: {date_str}"), meta_style))
            story.append(Spacer(1, 6))

            signature_style = ParagraphStyle(
                "SignatureStyle",
                parent=self.styles["Normal"],
                fontSize=9,
                textColor=colors.HexColor("#888888"),
            )
            story.append(Paragraph("Создано с помощью AI-Vera", signature_style))
            story.append(Spacer(1, 12))

            body_style = ParagraphStyle(
                "Body",
                parent=self.styles["Normal"],
                fontSize=11,
                leading=14,
            )

            for block in _split_into_paragraphs(text):
                # Paragraph ожидает мини-HTML, поэтому экранируем спецсимволы
                story.append(Paragraph(escape(block).replace("\n", "<br/>"), body_style))
                story.append(Spacer(1, 6))

            doc.build(story, onFirstPage=self._footer, onLaterPages=self._footer)
            return True
        except Exception as e:
            logger.error("Ошибка генерации PDF: %s", e, exc_info=True)
            return False


pdf_generator = PDFGenerator()

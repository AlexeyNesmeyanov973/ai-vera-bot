import logging
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from datetime import datetime

logger = logging.getLogger(__name__)

class PDFGenerator:
    """Генерация PDF с поддержкой кириллицы."""
    def __init__(self):
        self.styles = getSampleStyleSheet()
        self._setup_fonts()

    def _setup_fonts(self):
        try:
            pdfmetrics.registerFont(TTFont('DejaVuSans', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
            pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
            self.styles['Normal'].fontName = 'DejaVuSans'
            self.styles['Title'].fontName = 'DejaVuSans-Bold'
            self.styles['Heading1'].fontName = 'DejaVuSans-Bold'
        except Exception as e:
            logger.warning(f"Не удалось настроить шрифты: {e}")

    def generate_transcription_pdf(self, text: str, output_path: str, title: str = "Транскрибация"):
        try:
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=15*mm,
                leftMargin=15*mm,
                topMargin=15*mm,
                bottomMargin=15*mm
            )
            story = []

            title_style = self.styles['Title']
            story.append(Paragraph(title, title_style))
            story.append(Spacer(1, 12))

            date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
            date_style = ParagraphStyle('DateStyle', parent=self.styles['Normal'], fontSize=10, textColor=colors.HexColor('#666666'))
            story.append(Paragraph(f"Сгенерировано: {date_str}", date_style))
            story.append(Spacer(1, 8))

            signature_style = ParagraphStyle('SignatureStyle', parent=self.styles['Normal'], fontSize=9, textColor=colors.HexColor('#888888'))
            story.append(Paragraph("Создано с помощью AI-Vera Transcribator", signature_style))
            story.append(Spacer(1, 15))

            normal_style = self.styles['Normal']
            for paragraph in text.split('\n\n'):
                p = paragraph.strip()
                if p:
                    story.append(Paragraph(p.replace('\n', '<br/>'), normal_style))
                    story.append(Spacer(1, 8))

            doc.build(story)
            return True
        except Exception as e:
            logger.error(f"Ошибка генерации PDF: {e}")
            return False

pdf_generator = PDFGenerator()

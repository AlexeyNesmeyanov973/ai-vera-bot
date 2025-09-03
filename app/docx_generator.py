# app/docx_generator.py
import logging
from typing import List, Dict, Optional
from datetime import datetime

from docx import Document
from docx.shared import Pt, Cm
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH

logger = logging.getLogger(__name__)


def _fmt_ts(t: float) -> str:
    """HH:MM:SS для секунд с плавающей точкой."""
    t = float(t or 0.0)
    s = int(t) % 60
    m_total = int(t) // 60
    h = m_total // 60
    m = m_total % 60
    return f"{h:02}:{m:02}:{s:02}"


class DOCXGenerator:
    """
    Генерация DOCX:
      - обычный (сплошной текст)
      - со спикерами (speaker-labeled)
    """

    def __init__(self):
        pass

    def _base_doc(self, title: str) -> Document:
        doc = Document()

        # Поля страницы
        section = doc.sections[0]
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.0)
        section.right_margin = Cm(2.0)

        # Базовые стили: DejaVu Sans (есть в образе)
        styles = doc.styles

        # Normal
        normal = styles["Normal"]
        normal.font.name = "DejaVu Sans"
        normal.font.size = Pt(11)

        # Title
        if "Title" in styles:
            st = styles["Title"]
        else:
            st = styles.add_style("Title", WD_STYLE_TYPE.PARAGRAPH)
        st.font.name = "DejaVu Sans"
        st.font.bold = True
        st.font.size = Pt(20)

        # Subtitle
        if "Subtitle" in styles:
            sub = styles["Subtitle"]
        else:
            sub = styles.add_style("Subtitle", WD_STYLE_TYPE.PARAGRAPH)
        sub.font.name = "DejaVu Sans"
        sub.font.size = Pt(10)

        # SpeakerLabel
        if "SpeakerLabel" not in styles:
            sp = styles.add_style("SpeakerLabel", WD_STYLE_TYPE.CHARACTER)
            sp.font.name = "DejaVu Sans"
            sp.font.bold = True

        # Title block
        p = doc.add_paragraph(title, style="Title")
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        meta = f"Сгенерировано: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        doc.add_paragraph(meta, style="Subtitle")

        return doc

    def generate_plain_docx(self, text: str, output_path: str, title: str = "Транскрибация") -> bool:
        try:
            doc = self._base_doc(title)
            # Основной текст (разобьём по абзацам)
            for block in (text or "").split("\n\n"):
                b = block.strip()
                if not b:
                    continue
                # Сохраняем многострочность
                for line in b.split("\n"):
                    doc.add_paragraph(line)
                doc.add_paragraph("")  # пустая строка между абзацами

            doc.save(output_path)
            return True
        except Exception as e:
            logger.error(f"DOCX plain generation error: {e}")
            return False

    def generate_speaker_docx(
        self,
        segments: List[Dict],
        output_path: str,
        title: str = "Транскрибация",
        with_timestamps: bool = True,
    ) -> bool:
        """
        Ожидает сегменты вида:
          {"start": float, "end": float, "text": str, "speaker": "SPK1"}
        """
        try:
            doc = self._base_doc(title)

            # Сшиваем подряд сегменты одного и того же спикера (для читабельности)
            cur_spk: Optional[str] = None
            acc_lines: List[str] = []
            span_start: Optional[float] = None
            span_end: Optional[float] = None

            def flush():
                nonlocal cur_spk, acc_lines, span_start, span_end
                if not acc_lines:
                    return
                # Заголовок спикера
                hdr = doc.add_paragraph()
                run = hdr.add_run(cur_spk or "SPK")
                run.style = "SpeakerLabel"

                # Метки времени к группе
                if with_timestamps and span_start is not None and span_end is not None:
                    hdr.add_run(f"  [{_fmt_ts(span_start)}–{_fmt_ts(span_end)}]")

                # Текст группы (абзацем)
                body = " ".join(acc_lines).strip()
                doc.add_paragraph(body)
                doc.add_paragraph("")  # пустая строка между группами
                acc_lines = []
                span_start = None
                span_end = None

            for seg in segments or []:
                txt = (seg.get("text") or "").strip()
                if not txt:
                    continue
                spk = seg.get("speaker") or "SPK"
                s0 = float(seg.get("start") or 0.0)
                e0 = float(seg.get("end") or s0)

                if spk != cur_spk:
                    flush()
                    cur_spk = spk
                    span_start = s0
                    span_end = e0
                else:
                    # тот же спикер — расширяем временной диапазон
                    if span_start is None:
                        span_start = s0
                    span_end = e0

                acc_lines.append(txt)

            flush()
            doc.save(output_path)
            return True
        except Exception as e:
            logger.error(f"DOCX speakers generation error: {e}")
            return False


docx_generator = DOCXGenerator()

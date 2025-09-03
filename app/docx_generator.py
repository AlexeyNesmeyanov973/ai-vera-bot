# app/docx_generator.py
import logging
from typing import List, Dict, Optional
from datetime import datetime

from docx import Document
from docx.shared import Pt, Cm, RGBColor
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


# Палитра для спикеров (цикл по кругу)
PALETTE = [
    RGBColor(66, 133, 244),   # синий
    RGBColor(234, 67, 53),    # красный
    RGBColor(52, 168, 83),    # зелёный
    RGBColor(251, 188, 5),    # жёлтый/оранжевый
    RGBColor(171, 71, 188),   # фиолетовый
    RGBColor(0, 172, 193),    # бирюзовый
    RGBColor(255, 109, 0),    # оранжево-красный
    RGBColor(123, 31, 162),   # тёмно-фиолетовый
]


class DOCXGenerator:
    """
    Генерация DOCX:
      - generate_plain_docx: сплошной текст
      - generate_speaker_docx: подзаголовки спикеров с цветными маркерами
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

        # Заголовок спикера (параграфный стиль)
        if "SpeakerHeading" not in styles:
            sh = styles.add_style("SpeakerHeading", WD_STYLE_TYPE.PARAGRAPH)
            sh.font.name = "DejaVu Sans"
            sh.font.bold = True
            sh.font.size = Pt(13)
            # немного воздуха сверху/снизу
            sh.paragraph_format.space_before = Pt(6)
            sh.paragraph_format.space_after = Pt(2)

        # Сигнатура
        if "SpeakerLabel" not in styles:
            sp = styles.add_style("SpeakerLabel", WD_STYLE_TYPE.CHARACTER)
            sp.font.name = "DejaVu Sans"
            sp.font.bold = True

        # Титульная часть
        p = doc.add_paragraph(title, style="Title")
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        meta = f"Сгенерировано: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        doc.add_paragraph(meta, style="Subtitle")

        return doc

    def generate_plain_docx(self, text: str, output_path: str, title: str = "Транскрибация") -> bool:
        try:
            doc = self._base_doc(title)
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
        show_legend: bool = True,
        marker_char: str = "●",  # можно поменять на "■", "●", "◆"
    ) -> bool:
        """
        Ожидает сегменты вида:
          {"start": float, "end": float, "text": str, "speaker": "SPK1"}
        Формат:
          <цветной маркер> SPK1  [HH:MM:SS–HH:MM:SS]
          текст...
        """
        try:
            doc = self._base_doc(title)

            # Сшиваем подряд фразы одного спикера
            groups: List[Dict] = []
            cur_spk: Optional[str] = None
            acc_lines: List[str] = []
            span_start: Optional[float] = None
            span_end: Optional[float] = None

            def flush():
                nonlocal cur_spk, acc_lines, span_start, span_end
                if not acc_lines:
                    return
                groups.append({
                    "speaker": cur_spk or "SPK",
                    "start": float(span_start or 0.0),
                    "end": float(span_end or (span_start or 0.0)),
                    "text": " ".join(acc_lines).strip()
                })
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
                    if span_start is None:
                        span_start = s0
                    span_end = e0
                acc_lines.append(txt)
            flush()

            # Карта цветов спикеров по порядку появления
            speaker_order = []
            color_map: Dict[str, RGBColor] = {}
            for g in groups:
                spk = g["speaker"]
                if spk not in color_map:
                    speaker_order.append(spk)
                    color_map[spk] = PALETTE[(len(color_map)) % len(PALETTE)]

            # Легенда
            if show_legend and color_map:
                doc.add_paragraph("")  # отступ
                lg = doc.add_paragraph("Спикеры:", style="Subtitle")
                for spk in speaker_order:
                    p = doc.add_paragraph()
                    run_marker = p.add_run(f"{marker_char} ")
                    run_marker.font.bold = True
                    run_marker.font.color.rgb = color_map[spk]

                    run_name = p.add_run(spk)
                    run_name.bold = True

            # Контент по группам
            for g in groups:
                spk = g["speaker"]
                s0 = g["start"]
                e0 = g["end"]
                body = g["text"]

                # Подзаголовок спикера с цветным маркером
                hdr = doc.add_paragraph(style="SpeakerHeading")

                run_marker = hdr.add_run(marker_char + " ")
                run_marker.font.bold = True
                run_marker.font.color.rgb = color_map.get(spk, RGBColor(0, 0, 0))

                run_spk = hdr.add_run(spk)
                run_spk.bold = True

                if with_timestamps:
                    hdr.add_run(f"  [{_fmt_ts(s0)}–{_fmt_ts(e0)}]")

                # Текст группы — обычным абзацем
                # Сохраним переносы, если вдруг в тексте носители \n
                for i, line in enumerate(body.split("\n")):
                    doc.add_paragraph(line.strip())
                doc.add_paragraph("")  # пустая строка между блоками

            doc.save(output_path)
            return True
        except Exception as e:
            logger.error(f"DOCX speakers generation error: {e}")
            return False


docx_generator = DOCXGenerator()

# app/docx_generator.py
import logging
import os
from typing import List, Dict, Tuple, Iterable, Optional

logger = logging.getLogger(__name__)

try:
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except Exception as e:
    Document = None  # type: ignore
    logger.warning("python-docx не установлен. Добавьте 'python-docx' в requirements.txt")


# --------- ВСПОМОГАТЕЛЬНЫЕ ---------

def _fmt_hhmmss(t: float) -> str:
    t = max(0.0, float(t))
    s = int(t) % 60
    m = (int(t) // 60) % 60
    h = int(t) // 3600
    return f"{h:02}:{m:02}:{s:02}"

def _ensure_parent_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)

def _norm_text(s: str) -> str:
    return " ".join((s or "").replace("\u200b", "").split()).strip()

# фиксированный приятный набор цветов
_SPEAKER_COLORS = [
    RGBColor(33, 150, 243),   # синий
    RGBColor(244, 67, 54),    # красный
    RGBColor(76, 175, 80),    # зелёный
    RGBColor(255, 193, 7),    # янтарь
    RGBColor(156, 39, 176),   # фиолетовый
    RGBColor(0, 188, 212),    # бирюза
    RGBColor(255, 87, 34),    # оранжевый
    RGBColor(121, 85, 72),    # коричневый
    RGBColor(63, 81, 181),    # индиго
    RGBColor(139, 195, 74),   # лайм
]

def _speaker_color(speaker: str):
    idx = abs(hash(speaker)) % len(_SPEAKER_COLORS)
    return _SPEAKER_COLORS[idx]

def _speaker_key(seg: Dict) -> str:
    spk = (seg.get("speaker") or "").strip()
    return spk if spk else "SPK"

def _group_contiguous_by_speaker(segments: List[Dict]) -> List[Dict]:
    """
    Склеивает подряд идущие сегменты одного спикера в группы:
    [{speaker, start, end, texts: [..]}]
    """
    groups: List[Dict] = []
    cur: Optional[Dict] = None

    for seg in segments or []:
        text = _norm_text(seg.get("text") or "")
        if not text:
            continue
        spk = _speaker_key(seg)
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)

        if cur and cur["speaker"] == spk:
            cur["end"] = max(cur["end"], end)
            cur["texts"].append(text)
        else:
            if cur:
                groups.append(cur)
            cur = {"speaker": spk, "start": start, "end": end, "texts": [text]}

    if cur:
        groups.append(cur)
    return groups

def _collect_unique_speakers(segments: List[Dict]) -> List[str]:
    s = []
    seen = set()
    for seg in segments or []:
        spk = _speaker_key(seg)
        if spk not in seen:
            seen.add(spk)
            s.append(spk)
    return s


# --------- ОФОРМЛЕНИЕ WORD ---------

def _ensure_styles(doc, base_font: str = "DejaVu Sans"):
    """
    Настраиваем базовые стили: Normal, Title, SpeakerHeading, LegendHeading, LegendEntry.
    """
    styles = doc.styles

    # Normal
    normal = styles["Normal"]
    normal.font.name = base_font
    normal.font.size = Pt(11)

    # Title (встроенный)
    if "Title" in styles:
        styles["Title"].font.name = base_font

    # SpeakerHeading (параграфный)
    if "SpeakerHeading" not in styles:
        sp = styles.add_style("SpeakerHeading", WD_STYLE_TYPE.PARAGRAPH)
        sp.base_style = styles["Heading 2"] if "Heading 2" in styles else styles["Normal"]
        sp.font.name = base_font
        sp.font.bold = True
        sp.font.size = Pt(13)

    # LegendHeading
    if "LegendHeading" not in styles:
        lh = styles.add_style("LegendHeading", WD_STYLE_TYPE.PARAGRAPH)
        lh.base_style = styles["Heading 3"] if "Heading 3" in styles else styles["Normal"]
        lh.font.name = base_font
        lh.font.bold = True
        lh.font.size = Pt(12)

    # LegendEntry
    if "LegendEntry" not in styles:
        le = styles.add_style("LegendEntry", WD_STYLE_TYPE.PARAGRAPH)
        le.base_style = styles["Normal"]
        le.font.name = base_font
        le.font.size = Pt(11)


def _add_title_page(doc, title: str):
    p = doc.add_paragraph()
    run = p.add_run(title or "Транскрибация")
    p.style = doc.styles["Title"] if "Title" in doc.styles else doc.styles["Normal"]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    doc.add_paragraph("")  # отступ


def _add_legend(doc, speakers: List[str], marker_char: str = "●"):
    if not speakers:
        return
    doc.add_paragraph("Легенда", style="LegendHeading")
    for spk in speakers:
        line = doc.add_paragraph(style="LegendEntry")
        run_marker = line.add_run(f"{marker_char} ")
        color = _speaker_color(spk)
        run_marker.font.color.rgb = color
        run_marker.bold = True

        run_name = line.add_run(spk)
        run_name.bold = True
    doc.add_paragraph("")  # отступ


def _write_group(doc, group: Dict, with_timestamps: bool, marker_char: str):
    """
    Один блок: заголовок-спикер + его абзац(ы).
    """
    speaker = group["speaker"]
    start = group["start"]
    end = group["end"]
    texts = group["texts"]

    # Заголовок спикера
    head = doc.add_paragraph(style="SpeakerHeading")
    color = _speaker_color(speaker)

    run_marker = head.add_run(f"{marker_char} ")
    run_marker.font.color.rgb = color
    run_marker.bold = True

    run_name = head.add_run(speaker)
    run_name.bold = True

    if with_timestamps:
        head.add_run(f"  [{_fmt_hhmmss(start)}–{_fmt_hhmmss(end)}]").italic = True

    # Текст
    body_text = " ".join(texts).strip()
    # бьем по пустым строкам, если есть; иначе одним абзацем
    parts = [p.strip() for p in body_text.split("\n\n") if p.strip()] or [body_text]
    for par in parts:
        para = doc.add_paragraph()
        para.add_run(par)

    # небольшой отступ между блоками
    doc.add_paragraph("")


# --------- ПУБЛИЧНЫЙ ИНТЕРФЕЙС ---------

class DOCXGenerator:
    """
    Генератор DOCX:
      - generate_plain_docx(text, output_path, title)
      - generate_speaker_docx(segments, output_path, title, with_timestamps, show_legend, marker_char)
    """

    def generate_plain_docx(self, text: str, output_path: str, title: str = "Транскрибация") -> bool:
        if Document is None:
            logger.error("python-docx не установлен")
            return False
        try:
            _ensure_parent_dir(output_path)
            doc = Document()
            _ensure_styles(doc)

            _add_title_page(doc, title)

            # абзацы по двойному переносу
            blocks = [b.strip() for b in (text or "").split("\n\n") if b.strip()]
            if not blocks:
                blocks = [(text or "").strip()]

            for b in blocks:
                doc.add_paragraph(b)

            doc.save(output_path)
            return True
        except Exception:
            logger.exception("Ошибка генерации DOCX (plain)")
            return False

    def generate_speaker_docx(
        self,
        segments: List[Dict],
        output_path: str,
        title: str = "Транскрибация",
        with_timestamps: bool = True,
        show_legend: bool = True,
        marker_char: str = "●",
    ) -> bool:
        if Document is None:
            logger.error("python-docx не установлен")
            return False
        try:
            if not segments or not any((seg.get("speaker") or "").strip() for seg in segments):
                # нет диаризации — тревиально скатываемся в обычный DOCX
                full_text = " ".join(_norm_text(s.get("text") or "") for s in segments or []).strip()
                return self.generate_plain_docx(full_text, output_path, title=title)

            _ensure_parent_dir(output_path)
            doc = Document()
            _ensure_styles(doc)

            _add_title_page(doc, title)

            if show_legend:
                speakers = _collect_unique_speakers(segments)
                _add_legend(doc, speakers, marker_char=marker_char)

            groups = _group_contiguous_by_speaker(segments)
            for g in groups:
                _write_group(doc, g, with_timestamps=with_timestamps, marker_char=marker_char)

            doc.save(output_path)
            return True
        except Exception:
            logger.exception("Ошибка генерации DOCX (speakers)")
            return False


# Экспортируем удобный инстанс и функции-обёртки (совместимость с твоими вызовами)
docx_generator = DOCXGenerator()

def generate_plain_docx(text: str, output_path: str, title: str = "Транскрибация") -> bool:
    return docx_generator.generate_plain_docx(text, output_path, title=title)

def generate_speaker_docx(
    segments: List[Dict],
    output_path: str,
    title: str = "Транскрибация",
    with_timestamps: bool = True,
    show_legend: bool = True,
    marker_char: str = "●",
) -> bool:
    return docx_generator.generate_speaker_docx(
        segments=segments,
        output_path=output_path,
        title=title,
        with_timestamps=with_timestamps,
        show_legend=show_legend,
        marker_char=marker_char,
    )

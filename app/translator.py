# app/translator.py
import logging
from typing import List

from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

_MAX_CHARS = 4000  # безопасный размер для одного запроса

def _chunk(text: str, limit: int = _MAX_CHARS) -> List[str]:
    """Грубо бьём на куски по абзацам, чтобы не превышать лимит запроса."""
    if not text:
        return []
    parts, buf = [], []
    total = 0
    for paragraph in text.split("\n\n"):
        p = paragraph.strip()
        if not p:
            continue
        # +2 за разделитель между абзацами
        need = len(p) + (2 if buf else 0)
        if total + need > limit and buf:
            parts.append("\n\n".join(buf))
            buf, total = [p], len(p)
        else:
            if buf:
                buf.append(p)
                total += need
            else:
                buf = [p]
                total = len(p)
    if buf:
        parts.append("\n\n".join(buf))
    return parts

def translate_text(text: str, target_lang: str, source_lang: str = "auto") -> str:
    """
    Перевод текста на target_lang (без ключей).
    Делает чанкинг, чтобы не упереться в лимиты.
    """
    if not text:
        return ""
    try:
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        chunks = _chunk(text)
        out: List[str] = []
        for ch in chunks:
            out.append(translator.translate(ch))
        return "\n\n".join(out).strip()
    except Exception as e:
        logger.exception("Translation failed")
        raise RuntimeError(f"Translation error: {e}")

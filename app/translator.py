# app/translator.py
import logging
import time
from typing import List, Optional, Tuple

from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

_MAX_CHARS = 4000        # безопасный размер на батч
_RETRIES = 3             # число попыток
_BACKOFF_BASE = 0.7      # секунды (линейный бэкофф)
_LANG_ALIASES = {
    "ua": "uk",
    "cn": "zh-CN",
    "zh": "zh-CN",
    "iw": "he",
    "pt-br": "pt",
}

_TRANSLATOR_CACHE: dict[Tuple[Optional[str], str], GoogleTranslator] = {}


def _normalize_lang(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    c = code.strip().lower()
    return _LANG_ALIASES.get(c, c)


def _chunk(text: str, limit: int = _MAX_CHARS) -> List[str]:
    """Бьём на куски по абзацам, стараясь не превышать лимит."""
    if not text:
        return []
    parts, buf = [], []
    total = 0
    for paragraph in text.split("\n\n"):
        p = paragraph.strip()
        if not p:
            continue
        need = len(p) + (2 if buf else 0)  # запас на разделитель
        if buf and total + need > limit:
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


def _get_translator(source: Optional[str], target: str) -> GoogleTranslator:
    key = (source, target)
    tr = _TRANSLATOR_CACHE.get(key)
    if tr is None:
        tr = GoogleTranslator(source=source or "auto", target=target)
        _TRANSLATOR_CACHE[key] = tr
    return tr


def _retry_call(fn, *args, **kwargs):
    last_exc = None
    for attempt in range(1, _RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < _RETRIES:
                sleep_s = _BACKOFF_BASE * attempt
                time.sleep(sleep_s)
    raise last_exc  # пусть внешняя логика решит, что делать


def _translate_batch_safe(translator: GoogleTranslator, chunks: List[str]) -> List[str]:
    """Пробуем батч; при ошибке — поштучно с ретраями; при полном фейле — исходный текст."""
    if not chunks:
        return []

    # 1) Батч с ретраями
    try:
        res = _retry_call(translator.translate_batch, chunks)
        # deep_translator может вернуть строку для 1 элемента
        if isinstance(res, str):
            return [res]
        return [str(x) if x is not None else "" for x in res]
    except Exception as e:
        logger.warning(f"Batch translate failed, fallback to per-chunk: {e}")

    # 2) Поштучно с ретраями
    out: List[str] = []
    for ch in chunks:
        try:
            out.append(_retry_call(translator.translate, ch))
        except Exception as e:
            logger.error(f"Chunk translate failed, keeping original chunk: {e}")
            out.append(ch)  # мягкий фолбэк: возвращаем оригинал
    return out


def translate_text(text: str, target_lang: str, source_lang: str = "auto") -> str:
    """
    Перевод текста на target_lang.
    - Ретраи + бэкофф
    - Батч → поштучный фолбэк
    - На безрыбье возвращаем исходный текст без исключений
    """
    if not text:
        return ""

    target = _normalize_lang(target_lang) or "en"
    source = _normalize_lang(source_lang)

    # Если явный source совпадает с target — не переводим
    if source and source != "auto" and source == target:
        return text

    try:
        chunks = _chunk(text)
        translator = _get_translator(source, target)
        translated_list = _translate_batch_safe(translator, chunks)
        return "\n\n".join(translated_list).strip()
    except Exception as e:
        logger.exception(f"Translation failed hard, returning original: {e}")
        # Мягкий фолбэк — отдаём исходный текст
        return text

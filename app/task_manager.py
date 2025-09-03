# app/task_manager.py
import os
import math
import uuid
import asyncio
import logging
import tempfile
from typing import Dict, Any, List, Optional, Tuple

from pydub import AudioSegment

from app.downloaders import download_manager
from app.audio_processor import audio_processor
from app.limit_manager import limit_manager
from app.pdf_generator import pdf_generator

logger = logging.getLogger(__name__)


# ===== Настройки чанкования / таймаутов =====
# Максимальная длительность одного чанка (сек)
MAX_CHUNK_SEC = int(os.getenv("MAX_CHUNK_SEC", "60"))
# Пауза между чанками (мс) — помогает избежать склейки слов на стыке
CHUNK_CROSSFADE_MS = int(os.getenv("CHUNK_CROSSFADE_MS", "0"))
# Таймаут распознавания одного чанка (сек)
CHUNK_TIMEOUT_SEC = int(os.getenv("CHUNK_TIMEOUT_SEC", "240"))
# Общий таймаут задачи (сек)
TOTAL_TIMEOUT_SEC = int(os.getenv("TOTAL_TIMEOUT_SEC", "900"))
# Порог «большого файла» из Telegram (МБ)
TELEGRAM_MAX_MB = float(os.getenv("TELEGRAM_MAX_MB", "20"))
# Порог ссылки (МБ) — просто для сообщений пользователю
URL_MAX_MB = float(os.getenv("URL_MAX_MB", "500"))


def _normalize_can_process_resp(ret) -> Tuple[bool, str, Optional[int]]:
    """
    limit_manager.can_process(...) в проекте мог эволюционировать.
    Эта функция аккуратно нормализует ответ к (ok, message, remaining_after).
    """
    ok = False
    msg = ""
    remaining_after = None

    try:
        if isinstance(ret, tuple):
            # Берём первые три, остальное игнорируем
            if len(ret) >= 3:
                ok, msg, remaining_after = ret[0], ret[1], ret[2]
            elif len(ret) == 2:
                ok, msg = ret
            elif len(ret) == 1:
                ok = bool(ret[0])
        elif isinstance(ret, dict):
            ok = bool(ret.get("ok") or ret.get("can") or ret.get("allowed", False))
            msg = ret.get("message") or ret.get("error") or ""
            remaining_after = ret.get("remaining_after") or ret.get("remaining")
        else:
            ok = bool(ret)
    except Exception:
        ok = False
        msg = "internal"
        remaining_after = None

    return ok, msg, remaining_after


def _seconds_to_hhmmss(sec: float) -> str:
    s = int(round(sec))
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    return f"{h:02}:{m:02}:{ss:02}"


def _split_audio_to_chunks(wav_path: str, max_chunk_sec: int) -> List[Tuple[str, float, float]]:
    """
    Делит WAV на чанки одинаковой длительности (последний может быть короче).

    Возвращает список кортежей: (chunk_path, t_start_sec, t_end_sec)
    """
    audio = AudioSegment.from_file(wav_path)
    total_ms = len(audio)
    max_ms = max(1, max_chunk_sec * 1000)
    chunks: List[Tuple[str, float, float]] = []

    # Диапазоны [start, end)
    start = 0
    while start < total_ms:
        end = min(start + max_ms, total_ms)
        part = audio[start:end]
        # при необходимости можно добавить кроссфейд
        if CHUNK_CROSSFADE_MS > 0 and start > 0:
            part = audio[start - CHUNK_CROSSFADE_MS:end].fade_in(CHUNK_CROSSFADE_MS)

        tmp_dir = tempfile.gettempdir()
        chunk_name = f"chunk_{uuid.uuid4().hex}.wav"
        chunk_path = os.path.join(tmp_dir, chunk_name)
        part.export(chunk_path, format="wav")

        t0 = start / 1000.0
        t1 = end / 1000.0
        chunks.append((chunk_path, t0, t1))
        start = end

    return chunks


def _shift_segments(segments: List[Dict[str, Any]], shift_sec: float) -> List[Dict[str, Any]]:
    """
    Сдвигает таймкоды сегментов на shift_sec.
    """
    out = []
    for seg in segments or []:
        s = float(seg.get("start", 0.0)) + shift_sec
        e = float(seg.get("end", 0.0)) + shift_sec
        text = (seg.get("text") or "").strip()
        out.append({"start": s, "end": e, "text": text})
    return out


class TaskManager:
    """Менеджер для обработки задач транскрибации."""

    async def _transcribe_chunk(self, wav_path: str) -> Dict[str, Any]:
        """
        Обёртка над audio_processor.transcribe_audio с таймаутом.
        Возвращает результат распознавания (как отдаёт audio_processor).
        """
        return await asyncio.wait_for(
            asyncio.to_thread(audio_processor.transcribe_audio, wav_path),
            timeout=CHUNK_TIMEOUT_SEC,
        )

    async def _transcribe_with_chunking(self, wav_path: str) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Распознаёт wav с чанкованием и собирает общий текст и сегменты.
        Возвращает (full_text, full_segments).
        """
        chunks = _split_audio_to_chunks(wav_path, MAX_CHUNK_SEC)
        full_text_parts: List[str] = []
        full_segments: List[Dict[str, Any]] = []

        for idx, (cpath, t0, t1) in enumerate(chunks, start=1):
            try:
                logger.info(f"[chunk {idx}/{len(chunks)}] {_seconds_to_hhmmss(t0)}–{_seconds_to_hhmmss(t1)}")
                result = await self._transcribe_chunk(cpath)
                # Нормализуем в текст
                text_piece = audio_processor.format_transcription(result)
                if text_piece:
                    full_text_parts.append(text_piece)

                # Если есть сегменты — аккуратно сдвигаем
                segs = []
                try:
                    segs = result.get("segments") if isinstance(result, dict) else []
                except Exception:
                    segs = []
                if segs:
                    full_segments.extend(_shift_segments(segs, t0))

            except asyncio.TimeoutError:
                logger.warning(f"Timeout on chunk {idx}, skipping.")
                # Добавим маркер, чтобы пользователь видел разрыв
                full_text_parts.append(f"[... пропуск из-за таймаута на отрезке { _seconds_to_hhmmss(t0) }–{ _seconds_to_hhmmss(t1) } ...]")
            except Exception as e:
                logger.exception(f"Chunk {idx} error: {e}")
                full_text_parts.append(f"[... ошибка на отрезке { _seconds_to_hhmmss(t0) }–{ _seconds_to_hhmmss(t1) } ...]")
            finally:
                try:
                    if os.path.exists(cpath):
                        os.remove(cpath)
                except Exception:
                    pass

        return "\n".join([s for s in full_text_parts if s.strip()]), full_segments

    async def process_transcription_task(
        self,
        update,
        context,
        file_type: str,
        url: str = None
    ) -> Dict[str, Any]:
        """
        Основная задача для обработки медиафайла или ссылки.
        """

        user = update.effective_user
        user_id = user.id

        # общий таймаут задачи
        try:
            return await asyncio.wait_for(
                self._do_process(update, context, file_type, url, user_id),
                timeout=TOTAL_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            logger.error("Задача превысила общий таймаут")
            return {
                "success": False,
                "error": "timeout",
                "message": "⏳ Обработка заняла слишком много времени. Попробуйте меньший файл или пришлите ссылку."
            }
        except Exception as e:
            logger.exception("Ошибка в задаче транскрибации")
            return {"success": False, "error": str(e)}

    async def _do_process(
        self,
        update,
        context,
        file_type: str,
        url: Optional[str],
        user_id: int
    ) -> Dict[str, Any]:
        file_info = None
        wav_path = None

        try:
            # 1) Скачивание
            if url:
                file_info = await download_manager.download_from_url(url)
                if not file_info:
                    return {"success": False, "error": "download_failed", "is_url": True}
                is_url = True
            else:
                file_info = await download_manager.download_file(update, context, file_type)
                if not file_info:
                    return {"success": False, "error": "download_failed", "is_url": False}
                is_url = False

            # 2) Проверки размера (особенно для Telegram)
            size_mb = float(file_info.get("size_mb") or 0.0)
            if not is_url and size_mb > TELEGRAM_MAX_MB:
                # Подсказка на ссылку
                return {
                    "success": False,
                    "error": "file_too_big",
                    "message": (
                        f"❗️Файл {size_mb:.1f} МБ больше лимита Telegram ({TELEGRAM_MAX_MB:.0f} МБ).\n"
                        f"Пришлите ссылку на файл (до ~{int(URL_MAX_MB)} МБ) — я обработаю."
                    )
                }

            # 3) Лимиты пользователя
            duration_seconds = int(file_info.get("duration_seconds") or 0)
            ok, error_message, _ = _normalize_can_process_resp(
                limit_manager.can_process(user_id, duration_seconds)
            )
            if not ok:
                download_manager.cleanup_file(file_info["file_path"])
                return {"success": False, "error": "limit_exceeded", "message": error_message}

            # 4) Конвертация в WAV при необходимости
            src_path = file_info["file_path"]
            if not src_path.endswith(".wav"):
                wav_path = src_path + ".wav"
                if not download_manager.convert_to_wav(src_path, wav_path):
                    download_manager.cleanup_file(src_path)
                    return {"success": False, "error": "conversion_failed"}
            else:
                wav_path = src_path

            # 5) Распознавание (с чанкованием)
            full_text, full_segments = await self._transcribe_with_chunking(wav_path)

            # 6) Обновляем использование времени
            try:
                limit_manager.update_usage(user_id, duration_seconds)
            except Exception:
                logger.exception("Не удалось обновить usage, но продолжаем")

            # 7) Авто-PDF если длинный текст
            pdf_path = None
            if len(full_text) > 1000:
                try:
                    pdf_path = wav_path + ".pdf"
                    title = file_info.get("title", "Транскрибация")
                    pdf_generator.generate_transcription_pdf(full_text, pdf_path, title)
                    if not os.path.exists(pdf_path):
                        pdf_path = None
                except Exception:
                    logger.exception("Ошибка генерации PDF")
                    pdf_path = None

            # 8) Готовим ответ
            result = {
                "success": True,
                "text": full_text or "",
                "segments": full_segments or [],
                "duration": duration_seconds,
                "user_id": user_id,
                "file_type": file_type,
                "is_url": bool(url),
                "pdf_path": pdf_path,
                "title": file_info.get("title", "Файл"),
            }
            return result

        finally:
            # 9) Уборка временных файлов
            try:
                if file_info and file_info.get("file_path"):
                    download_manager.cleanup_file(file_info["file_path"])
            except Exception:
                pass
            try:
                if wav_path and os.path.exists(wav_path) and file_info and wav_path != file_info.get("file_path"):
                    download_manager.cleanup_file(wav_path)
            except Exception:
                pass


# Глобальный экземпляр менеджера задач
task_manager = TaskManager()

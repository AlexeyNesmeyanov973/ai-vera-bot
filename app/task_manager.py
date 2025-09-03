# app/task_manager.py
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

from app.config import (
    WHISPER_LANGUAGE,
    MAX_FILE_SIZE_MB,
    URL_MAX_FILE_SIZE_MB,
)
from app.utils import format_seconds
from app import storage
from app.downloaders import (
    download_from_telegram,
    download_from_url,
)
from app.pdf_generator import pdf_generator

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    success: bool
    text: str = ""
    segments: Optional[List[Dict]] = None
    title: Optional[str] = None
    duration: float = 0.0
    pdf_path: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None


class TaskManager:
    """
    Высокоуровневая обвязка:
      1) Скачать входной файл (из TG или по ссылке)
      2) (Опционально) чанковать медиа (ffmpeg)
      3) Транскрибировать (через app.audio_processor)
      4) Собрать итог + сохранить PDF
      5) Списать минуты и вернуть результат
    """

    def __init__(self):
        # ленивый импорт — чтобы не держать модель в памяти преждевременно
        self._audio = None

    # -------- внутреннее --------

    def _ensure_audio(self):
        if self._audio is None:
            # app.audio_processor должен предоставлять:
            #   transcribe(path, language=...) -> {text, segments, duration, title}
            # и сам подхватывать backend (faster-whisper/openai-whisper) из конфигов.
            from app import audio_processor
            self._audio = audio_processor

    def _safe_tmpdir(self) -> str:
        # уважаем TMP_DIR из конфига, если задан (fallback — системный tmp)
        try:
            from app.config import TMP_DIR
        except Exception:
            TMP_DIR = None
        base = TMP_DIR if TMP_DIR else tempfile.gettempdir()
        d = os.path.join(base, "ai_vera_jobs")
        os.makedirs(d, exist_ok=True)
        return d

    def _chunk_media(self, src_path: str, max_minutes: int = 30) -> List[str]:
        """
        Делит входной файл на куски по max_minutes (если есть ffmpeg).
        Если ffmpeg недоступен — возвращает исходный файл одним куском.
        """
        try:
            # проверим наличие ffmpeg
            completed = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True
            )
            if completed.returncode != 0:
                logger.warning("ffmpeg не найден — чанкование пропущено")
                return [src_path]
        except Exception:
            logger.warning("ffmpeg не найден — чанкование пропущено (исключение)")
            return [src_path]

        out_dir = os.path.join(self._safe_tmpdir(), f"chunks_{uuid.uuid4().hex[:8]}")
        os.makedirs(out_dir, exist_ok=True)
        # сегментация по времени: segment_time = max_minutes * 60
        out_tpl = os.path.join(out_dir, "part_%03d.mp4")
        seg_time = str(max_minutes * 60)

        # Без повторной компрессии (-c copy). Для некоторых контейнеров может не сработать — fallback ниже.
        cmd = [
            "ffmpeg", "-y", "-i", src_path,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", seg_time,
            "-reset_timestamps", "1",
            out_tpl
        ]
        logger.info("FFmpeg segment cmd: %s", " ".join(cmd))
        rc = subprocess.call(cmd)
        chunked = sorted(
            [os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith("part_")]
        )
        if rc == 0 and chunked:
            return chunked

        # Fallback: перекодирование в WAV с разбиением на куски
        logger.info("Пробую перекодирование и чанкование WAV...")
        out_tpl = os.path.join(out_dir, "part_%03d.wav")
        cmd = [
            "ffmpeg", "-y", "-i", src_path,
            "-f", "segment",
            "-segment_time", seg_time,
            "-ac", "1", "-ar", "16000",
            out_tpl
        ]
        rc = subprocess.call(cmd)
        chunked = sorted(
            [os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith("part_")]
        )
        if rc == 0 and chunked:
            return chunked

        logger.warning("Не удалось разрезать файл — используем оригинал.")
        shutil.rmtree(out_dir, ignore_errors=True)
        return [src_path]

    def _merge_texts(self, parts: List[Dict]) -> Tuple[str, List[Dict], float]:
        """
        parts: [{text, segments, duration}]
        Склеиваем текст и сдвигаем таймкоды для SRT.
        """
        full_text = []
        all_segments: List[Dict] = []
        time_offset = 0.0
        total_duration = 0.0

        for p in parts:
            t = (p.get("text") or "").strip()
            if t:
                full_text.append(t)
            segs = p.get("segments") or []
            for s in segs:
                s = dict(s)
                s["start"] = float(s.get("start", 0.0)) + time_offset
                s["end"] = float(s.get("end", 0.0)) + time_offset
                all_segments.append(s)
            d = float(p.get("duration") or 0.0)
            time_offset += d
            total_duration += d

        return ("\n\n".join(full_text).strip(), all_segments, total_duration)

    # -------- публичное API — вызывается из очереди --------

    async def process_transcription_task(self, update, context, file_type: str, url: str | None = None) -> Dict:
        """
        Главная корутина для очереди.
        Возвращает словарь, который бот дальше обрабатывает и показывает пользователю.
        """
        from app.limit_manager import limit_manager  # локально, чтобы избежать циклических импортов

        user = update.effective_user
        user_id = user.id

        tmp_dir = self._safe_tmpdir()
        work_id = uuid.uuid4().hex[:8]
        local_path = None
        downloaded_title = None
        media_duration = 0.0

        # 1) Скачать источник
        try:
            if file_type == "url" and url:
                info = await download_from_url(
                    url=url,
                    dest_dir=tmp_dir,
                    max_size_mb=float(URL_MAX_FILE_SIZE_MB),
                )
            else:
                info = await download_from_telegram(
                    update=update,
                    context=context,
                    file_type=file_type,
                    dest_dir=tmp_dir,
                    max_size_mb=float(MAX_FILE_SIZE_MB),
                )

            if not info.get("success"):
                return {
                    "success": False,
                    "error": "download_failed",
                    "message": info.get("error") or "Не удалось получить медиа",
                }

            local_path = info["path"]
            downloaded_title = info.get("title")
            media_duration = float(info.get("duration") or 0.0)
            size_mb = float(info.get("file_size_mb") or 0.0)
            logger.info("Файл получен: %s (≈%.1f МБ)", local_path, size_mb)
        except Exception as e:
            logger.exception("Ошибка скачивания")
            return {
                "success": False,
                "error": "download_failed",
                "message": str(e),
            }

        # 2) Проверка лимитов/покупок (предварительно — по известной длительности из TG/yt-dlp).
        # Если длительность неизвестна (0), пропускаем пречек и спишем по факту после транскрибации.
        try:
            if media_duration and media_duration > 0:
                ok, msg, _remain, _deficit = limit_manager.can_process(user_id, int(media_duration))
                if not ok:
                    return {
                        "success": False,
                        "error": "limit_exceeded",
                        "message": msg or "Лимит исчерпан",
                    }
        except Exception:
            logger.exception("Ошибка проверки лимита (precheck)")

        # 3) Чанковать большой файл (напр. по 30 минут)
        chunks = [local_path]
        try:
            chunks = self._chunk_media(local_path, max_minutes=30)
        except Exception:
            logger.exception("Ошибка чанкования — продолжу одним файлом")

        # 4) Транскрибировать
        self._ensure_audio()
        per_parts: List[Dict] = []
        language = None if WHISPER_LANGUAGE == "auto" else WHISPER_LANGUAGE

        try:
            for idx, cpath in enumerate(chunks, start=1):
                logger.info("Транскрибация %s/%s: %s", idx, len(chunks), cpath)
                piece = await self._audio.transcribe(cpath, language=language)
                # Ожидаем, что piece = {text, segments, duration, title?}
                per_parts.append(piece)
        except Exception as e:
            logger.exception("Ошибка транскрибации")
            return {
                "success": False,
                "error": "transcribe_failed",
                "message": str(e),
            }

        # 5) Склеить результаты
        full_text, all_segments, total_duration = self._merge_texts(per_parts)
        title = downloaded_title or per_parts[0].get("title") or "Транскрибация"

        # 6) Списать минуты (по факту длительности, округляя до секунд)
        try:
            from app.limit_manager import limit_manager
            sec = int(round(total_duration or media_duration or 0))
            if sec > 0:
                # если пречек не делали (media_duration == 0), то проверим постфактум
                if not media_duration:
                    ok, msg, _remain, _deficit = limit_manager.can_process(user_id, sec)
                    if not ok:
                        return {
                            "success": False,
                            "error": "limit_exceeded",
                            "message": msg or "Лимит исчерпан",
                        }
                limit_manager.update_usage(user_id, sec)
        except Exception:
            logger.exception("Не удалось применить списание минут (apply_usage_seconds)")

        # 7) Сгенерировать PDF (по желанию — сразу, для удобства)
        pdf_path = None
        try:
            # Генератор может сам создать каталог
            out_dir = os.path.join(self._safe_tmpdir(), "pdfs")
            os.makedirs(out_dir, exist_ok=True)
            pdf_path = os.path.join(out_dir, f"transcription_{work_id}.pdf")
            pdf_generator.generate_transcription_pdf(full_text, pdf_path, title=title)
        except Exception:
            logger.exception("Не удалось сгенерировать PDF")
            pdf_path = None

        # 8) Итог
        return {
            "success": True,
            "text": full_text,
            "segments": all_segments,
            "title": title,
            "duration": total_duration,
            "pdf_path": pdf_path,
        }


task_manager = TaskManager()

# app/task_manager.py
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

from app.config import (
    WHISPER_LANGUAGE,
    MAX_FILE_SIZE_MB,
    URL_MAX_FILE_SIZE_MB,
)
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
    detected_language: Optional[str] = None
    processing_time_s: Optional[float] = None


class TaskManager:
    """
    1) Скачать входной файл (TG/URL)
    2) (Опц.) разрезать ffmpeg
    3) Транскрибировать
    4) Склеить и сдвинуть таймкоды
    5) Списать минуты, сгенерить PDF
    6) Вернуть результат
    """

    def __init__(self):
        self._audio = None

    def _ensure_audio(self):
        if self._audio is None:
            from app import audio_processor
            self._audio = audio_processor

    def _safe_tmpdir(self) -> str:
        try:
            from app.config import TMP_DIR
        except Exception:
            TMP_DIR = None
        base = TMP_DIR if TMP_DIR else tempfile.gettempdir()
        d = os.path.join(base, "ai_vera_jobs")
        os.makedirs(d, exist_ok=True)
        return d

    def _chunk_media(self, src_path: str, max_minutes: int = 30) -> List[str]:
        try:
            completed = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
            if completed.returncode != 0:
                logger.warning("ffmpeg не найден — чанкование пропущено")
                return [src_path]
        except Exception:
            logger.warning("ffmpeg не найден — чанкование пропущено (исключение)")
            return [src_path]

        out_dir = os.path.join(self._safe_tmpdir(), f"chunks_{uuid.uuid4().hex[:8]}")
        os.makedirs(out_dir, exist_ok=True)
        out_tpl = os.path.join(out_dir, "part_%03d.mp4")
        seg_time = str(max_minutes * 60)

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
        chunked = sorted([os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith("part_")])
        if rc == 0 and chunked:
            return chunked

        # Fallback: WAV
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
        chunked = sorted([os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith("part_")])
        if rc == 0 and chunked:
            return chunked

        logger.warning("Не удалось разрезать файл — используем оригинал.")
        shutil.rmtree(out_dir, ignore_errors=True)
        return [src_path]

    def _merge_texts(self, parts: List[Dict]) -> Tuple[str, List[Dict], float, Optional[str]]:
        """
        parts ~ [{text, segments, duration, language?}]
        Возвращает: full_text, all_segments, total_duration, detected_language
        """
        full_text = []
        all_segments: List[Dict] = []
        time_offset = 0.0
        total_duration = 0.0

        # детекция языка — возьмём первый ненулевой
        detected_language = None

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

            if not detected_language:
                lang = p.get("language") or p.get("detected_language")
                if lang:
                    detected_language = str(lang)

        return ("\n\n".join(full_text).strip(), all_segments, total_duration, detected_language)

    async def process_transcription_task(self, update, context, file_type: str, url: str | None = None) -> Dict:
        from app.limit_manager import limit_manager  # локально, чтобы избежать циклических импортов

        user = update.effective_user
        user_id = user.id

        tmp_dir = self._safe_tmpdir()
        work_id = uuid.uuid4().hex[:8]
        local_path = None
        downloaded_title = None
        media_duration = 0.0

        # будем убирать всё в конце
        temp_paths: List[str] = []
        temp_dirs: List[str] = []

        start_ts = time.time()

        try:
            # 1) Скачать
            if file_type == "url" and url:
                info = await download_from_url(url=url, dest_dir=tmp_dir, max_size_mb=float(URL_MAX_FILE_SIZE_MB))
            else:
                info = await download_from_telegram(
                    update=update, context=context, file_type=file_type,
                    dest_dir=tmp_dir, max_size_mb=float(MAX_FILE_SIZE_MB),
                )

            if not info.get("success"):
                return {"success": False, "error": "download_failed", "message": info.get("error") or "Не удалось получить медиа"}

            local_path = info["path"]
            temp_paths.append(local_path)
            downloaded_title = info.get("title")
            media_duration = float(info.get("duration") or 0.0)
            size_mb = float(info.get("file_size_mb") or 0.0)
            logger.info("Файл получен: %s (≈%.1f МБ)", local_path, size_mb)

            # 2) Пречек лимитов (если знаем длительность)
            try:
                if media_duration and media_duration > 0:
                    ok, msg, _remain, _deficit = limit_manager.can_process(user_id, int(media_duration))
                    if not ok:
                        return {"success": False, "error": "limit_exceeded", "message": msg or "Лимит исчерпан"}
            except Exception:
                logger.exception("Ошибка проверки лимита (precheck)")

            # 3) Чанковать
            chunks = [local_path]
            try:
                chunks = self._chunk_media(local_path, max_minutes=30)
                # если получили отдельную директорию с частями — уберём потом
                if len(chunks) > 1:
                    temp_dirs.append(os.path.dirname(chunks[0]))
            except Exception:
                logger.exception("Ошибка чанкования — продолжу одним файлом")

            # 4) Транскрибация
            self._ensure_audio()
            per_parts: List[Dict] = []
            language = None if WHISPER_LANGUAGE == "auto" else WHISPER_LANGUAGE

            for idx, cpath in enumerate(chunks, start=1):
                logger.info("Транскрибация %s/%s: %s", idx, len(chunks), cpath)
                piece = await self._audio.transcribe(cpath, language=language)
                per_parts.append(piece)

            # 5) Склейка
            full_text, all_segments, total_duration, detected_language = self._merge_texts(per_parts)
            title = downloaded_title or per_parts[0].get("title") or "Транскрибация"

            # Подсчёт слов (грубый, но быстрый)
            try:
                word_count = len((full_text or "").split())
            except Exception:
                word_count = 0

            # 6) Списание по факту
            try:
                sec = int(round(total_duration or media_duration or 0))
                if sec > 0:
                    if not media_duration:
                        ok, msg, _remain, _deficit = limit_manager.can_process(user_id, sec)
                        if not ok:
                            return {"success": False, "error": "limit_exceeded", "message": msg or "Лимит исчерпан"}
                    limit_manager.update_usage(user_id, sec)
            except Exception:
                logger.exception("Не удалось применить списание минут (update_usage)")

            # 7) PDF
            pdf_path = None
            try:
                out_dir = os.path.join(self._safe_tmpdir(), "pdfs")
                os.makedirs(out_dir, exist_ok=True)
                pdf_path = os.path.join(out_dir, f"transcription_{work_id}.pdf")
                pdf_generator.generate_transcription_pdf(full_text, pdf_path, title=title)
            except Exception:
                logger.exception("Не удалось сгенерировать PDF")
                pdf_path = None

            processing_time_s = round(time.time() - start_ts, 2)

            # 8) Итог
            return {
                "success": True,
                "text": full_text,
                "segments": all_segments,
                "title": title,
                "duration": total_duration,
                "pdf_path": pdf_path,
                "detected_language": detected_language,
                "processing_time_s": processing_time_s,
                "word_count": word_count,
            }

        except Exception as e:
            logger.exception("Критическая ошибка обработки")
            return {"success": False, "error": "transcribe_failed", "message": str(e)}

        finally:
            # Уборка: удаляем исходник и каталоги чанков (PDF оставляем для экспорта)
            for p in temp_paths:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
            for d in temp_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass


task_manager = TaskManager()

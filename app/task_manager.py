# app/task_manager.py
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
import time
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

from app.config import (
    WHISPER_LANGUAGE,
    MAX_FILE_SIZE_MB,
    URL_MAX_FILE_SIZE_MB,
)
from app import storage  # может использоваться в расширениях
from app.downloaders import (
    download_from_telegram,
    download_from_url,
)
from app.pdf_generator import pdf_generator
from app.utils import get_audio_duration  # для оценки длительности локального файла
from app.diarizer import diarizer

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    success: bool
    text: str = ""
    segments: Optional[List[Dict]] = None
    title: Optional[str] = None
    duration: float = 0.0
    pdf_path: Optional[str] = None
    detected_language: Optional[str] = None
    word_count: Optional[int] = None
    processing_time_s: Optional[float] = None
    error: Optional[str] = None
    message: Optional[str] = None


def _attach_speakers_to_segments(
    segments: List[Dict],
    diarization_turns: List[Dict],
    default_speaker: str = "SPK",
) -> List[Dict]:
    """
    Назначает каждому сегменту лучшего спикера на основе максимального перекрытия
    с интервалами диаризации.
    segments: [{start, end, text, ...}]
    diarization_turns: [{start, end, speaker}, ...]
    """
    if not segments or not diarization_turns:
        # проставим дефолт там, где пусто
        for seg in segments or []:
            seg.setdefault("speaker", default_speaker)
        return segments

    # сгруппируем интервалы диаризации по спикерам
    diar_by_spk: Dict[str, List[tuple[float, float]]] = {}
    for turn in diarization_turns:
        spk = str(turn.get("speaker") or "").strip() or default_speaker
        try:
            ds = float(turn.get("start", 0.0))
            de = float(turn.get("end", ds))
        except Exception:
            continue
        if de < ds:
            ds, de = de, ds
        diar_by_spk.setdefault(spk, []).append((ds, de))

    for spk in diar_by_spk:
        diar_by_spk[spk].sort()

    def _overlap(a: float, b: float, c: float, d: float) -> float:
        lo = max(a, c)
        hi = min(b, d)
        return (hi - lo) if hi > lo else 0.0

    for seg in segments:
        try:
            s0 = float(seg.get("start", 0.0))
            e0 = float(seg.get("end", s0))
        except Exception:
            s0, e0 = 0.0, 0.0

        txt = (seg.get("text") or "").strip()
        if not txt:
            seg["speaker"] = seg.get("speaker") or default_speaker
            continue

        best_spk = None
        best_ovl = 0.0
        for spk, ivals in diar_by_spk.items():
            ovl = 0.0
            for ds, de in ivals:
                ovl += _overlap(s0, e0, ds, de)
                if ds > e0:
                    break
            if ovl > best_ovl:
                best_ovl = ovl
                best_spk = spk

        seg["speaker"] = best_spk or seg.get("speaker") or default_speaker

    return segments


class TaskManager:
    """
    1) Скачать входной файл
    2) (Опц.) Разбить на куски ffmpeg
    3) Транскрибировать
    4) Собрать итог + сохранить PDF
    5) Списать минуты и вернуть результат
    """

    def __init__(self):
        self._audio = None  # ленивый импорт

    def _ensure_audio(self):
        if self._audio is None:
            from app import audio_processor
            self._audio = audio_processor

    def _safe_tmpdir(self) -> str:
        d = os.path.join(tempfile.gettempdir(), "ai_vera_jobs")
        os.makedirs(d, exist_ok=True)
        return d

    def _chunk_media(self, src_path: str, max_minutes: int = 30) -> List[str]:
        """Разбиваем медиа на куски по max_minutes при наличии ffmpeg."""
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

        seg_time = str(max_minutes * 60)
        out_tpl = os.path.join(out_dir, "part_%03d.mp4")

        # быстрый путь: без перекодирования
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

        # fallback: в WAV с разбиением
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

    def _merge_texts(self, parts: List[Dict]) -> Tuple[str, List[Dict], float, Optional[str], int]:
        """
        parts: [{text, segments, duration, language}]
        Возвращает: (text, segments, total_duration, detected_language, word_count)
        """
        full_text: List[str] = []
        all_segments: List[Dict] = []
        time_offset = 0.0
        total_duration = 0.0
        detected_language = None

        for idx, p in enumerate(parts):
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
            if idx == 0:
                detected_language = p.get("language") or None

        text_joined = "\n\n".join(full_text).strip()
        # грубый подсчёт слов
        word_count = len([w for w in text_joined.split() if any(ch.isalnum() for ch in w)])

        return text_joined, all_segments, total_duration, detected_language, word_count

    async def process_transcription_task(self, update, context, file_type: str, url: str | None = None) -> Dict:
        from app.limit_manager import limit_manager  # локальный импорт, чтобы избежать циклов

        user_id = update.effective_user.id

        tmp_dir = self._safe_tmpdir()
        work_id = uuid.uuid4().hex[:8]
        local_path = None
        downloaded_title = None
        media_duration = 0.0

        # 1) Скачать источник
        try:
            if file_type == "url" and url:
                info = await download_from_url(url=url, dest_dir=tmp_dir, max_size_mb=float(URL_MAX_FILE_SIZE_MB))
            else:
                info = await download_from_telegram(update=update, context=context, file_type=file_type,
                                                    dest_dir=tmp_dir, max_size_mb=float(MAX_FILE_SIZE_MB))

            if not info.get("success"):
                return {"success": False, "error": "download_failed", "message": info.get("error") or "Не удалось получить медиа"}

            local_path = info["path"]
            downloaded_title = info.get("title")
            media_duration = float(info.get("duration") or 0.0)
            size_mb = float(info.get("file_size_mb") or 0.0)
            logger.info("Файл получен: %s (≈%.1f МБ)", local_path, size_mb)
        except Exception as e:
            logger.exception("Ошибка скачивания")
            return {"success": False, "error": "download_failed", "message": str(e)}

        # если телега/yt не дали длительность — попробуем оценить из файла
        if media_duration <= 0:
            try:
                media_duration = float(get_audio_duration(local_path))
            except Exception:
                media_duration = 0.0  # пойдём дальше, окончательно спишем по факту

        # 2) Проверка лимитов
        ok, error_message, _, _ = limit_manager.can_process(user_id, int(media_duration) if media_duration > 0 else 0)
        if not ok:
            return {"success": False, "error": "limit_exceeded", "message": error_message or "Лимит исчерпан"}

        # 3) Чанковать большой файл (по 30 минут)
        try:
            chunks = self._chunk_media(local_path, max_minutes=30)
        except Exception:
            logger.exception("Ошибка чанкования — продолжу одним файлом")
            chunks = [local_path]

        # 4) Транскрибация
        self._ensure_audio()
        per_parts: List[Dict] = []
        language = None if WHISPER_LANGUAGE == "auto" else WHISPER_LANGUAGE

        started = time.perf_counter()
        try:
            for idx, cpath in enumerate(chunks, start=1):
                logger.info("Транскрибация %s/%s: %s", idx, len(chunks), cpath)
                piece = await self._audio.transcribe(cpath, language=language)
                # ожидаем {text, segments, duration, language?, title?}
                per_parts.append(piece)
        except Exception as e:
            logger.exception("Ошибка транскрибации")
            return {"success": False, "error": "transcribe_failed", "message": str(e)}
        processing_time_s = time.perf_counter() - started

        # 5) Склейка результатов + метрики
        full_text, all_segments, total_duration, detected_language, word_count = self._merge_texts(per_parts)
        title = downloaded_title or per_parts[0].get("title") or "Транскрибация"

        # 5b) Диаризация спикеров (опционально)
        try:
            diar = diarizer.diarize(local_path)
            if diar:
                all_segments = _attach_speakers_to_segments(all_segments, diar)
        except Exception:
            logger.exception("Speaker attribution failed")

        # 6) Списать минуты по фактической длительности
        try:
            limit_manager.update_usage(user_id=user_id, additional_seconds=int(total_duration))
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

        # 8) Итог
        return {
            "success": True,
            "text": full_text,
            "segments": all_segments,
            "title": title,
            "duration": float(total_duration),
            "pdf_path": pdf_path,
            "detected_language": detected_language,
            "word_count": int(word_count),
            "processing_time_s": float(processing_time_s),
        }


task_manager = TaskManager()

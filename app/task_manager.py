# app/task_manager.py
import os
import logging
from typing import Dict, Any, Optional, List
from pydub import AudioSegment

from app.downloaders import download_manager
from app.audio_processor import audio_processor
from app.limit_manager import limit_manager
from app.pdf_generator import pdf_generator

logger = logging.getLogger(__name__)

# Длительность чанка (в секундах)
CHUNK_DURATION = 300  # 5 минут


class TaskManager:
    """Менеджер для обработки задач транскрибации с поддержкой чанкования."""

    def _chunk_wav(self, wav_path: str) -> List[str]:
        """
        Делит WAV-файл на чанки по CHUNK_DURATION секунд.
        Возвращает список временных путей к чанкам.
        """
        audio = AudioSegment.from_wav(wav_path)
        total_ms = len(audio)
        chunk_paths = []

        for i, start_ms in enumerate(range(0, total_ms, CHUNK_DURATION * 1000)):
            end_ms = min(start_ms + CHUNK_DURATION * 1000, total_ms)
            chunk = audio[start_ms:end_ms]

            chunk_path = f"{wav_path}.part{i}.wav"
            chunk.export(chunk_path, format="wav", parameters=["-ac", "1", "-ar", "16000"])
            chunk_paths.append(chunk_path)

        return chunk_paths

    async def process_transcription_task(
        self, update, context, file_type: str, url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Обработка медиафайла или ссылки (с чанкованием длинных файлов).
        """
        user = update.effective_user
        user_id = user.id

        file_info: Optional[Dict[str, Any]] = None
        wav_path: Optional[str] = None
        chunk_paths: List[str] = []

        try:
            # 1) Загрузка
            if url:
                file_info = await download_manager.download_from_url(url)
                if not file_info:
                    return {"success": False, "error": "download_failed", "is_url": True}
            else:
                file_info = await download_manager.download_file(update, context, file_type)
                if not file_info:
                    return {"success": False, "error": "download_failed", "is_url": False}

            # 2) Проверка лимитов
            ok, error_message, _remaining_after = limit_manager.can_process(
                user_id, file_info["duration_seconds"]
            )
            if not ok:
                return {"success": False, "error": "limit_exceeded", "message": error_message}

            # 3) Конвертация в WAV
            src_path = file_info["file_path"]
            if not src_path.lower().endswith(".wav"):
                wav_path = src_path + ".wav"
                if not download_manager.convert_to_wav(src_path, wav_path):
                    return {"success": False, "error": "conversion_failed"}
            else:
                wav_path = src_path

            # 4) Чанкование WAV
            chunk_paths = self._chunk_wav(wav_path)

            # 5) Транскрибация чанков
            full_text_parts: List[str] = []
            full_segments: List[Dict[str, Any]] = []
            offset = 0.0
            language = None

            for idx, chunk_path in enumerate(chunk_paths):
                logger.info(f"Обработка чанка {idx+1}/{len(chunk_paths)}: {chunk_path}")
                result = audio_processor.transcribe_audio(chunk_path)
                text = result.get("text", "").strip()
                segments = result.get("segments", [])

                # Сдвигаем тайм-коды
                for seg in segments:
                    seg["start"] += offset
                    seg["end"] += offset
                    full_segments.append(seg)

                if text:
                    full_text_parts.append(text)
                if not language and result.get("language"):
                    language = result.get("language")

                offset += CHUNK_DURATION

            transcription_result = {
                "text": " ".join(full_text_parts).strip(),
                "segments": full_segments,
                "language": language,
            }

            transcription_text = audio_processor.format_transcription(transcription_result)

            # 6) Обновляем лимиты
            limit_manager.update_usage(user_id, file_info["duration_seconds"])

            # 7) PDF (по желанию)
            pdf_path = None
            if len(transcription_text) > 1000:
                pdf_path = wav_path + ".pdf"
                title = file_info.get("title", "Транскрибация")
                pdf_generator.generate_transcription_pdf(transcription_text, pdf_path, title)

            return {
                "success": True,
                "text": transcription_text,
                "segments": full_segments,
                "duration": file_info["duration_seconds"],
                "user_id": user_id,
                "file_type": file_type,
                "is_url": bool(url),
                "pdf_path": pdf_path if pdf_path and os.path.exists(pdf_path) else None,
                "title": file_info.get("title", "Файл"),
            }

        except Exception as e:
            logger.exception("Ошибка в задаче транскрибации")
            return {"success": False, "error": str(e)}

        finally:
            # Очистка файлов
            try:
                if file_info and file_info.get("file_path"):
                    if wav_path and file_info["file_path"] != wav_path:
                        os.remove(file_info["file_path"])
                if wav_path and os.path.exists(wav_path):
                    os.remove(wav_path)
                for cp in chunk_paths:
                    if os.path.exists(cp):
                        os.remove(cp)
            except Exception as e:
                logger.debug(f"Ошибка очистки файлов: {e}")


# Глобальный экземпляр менеджера задач
task_manager = TaskManager()

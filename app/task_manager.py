# app/task_manager.py
import os
import logging
from typing import Dict, Any, Optional

from app.downloaders import download_manager
from app.audio_processor import audio_processor
from app.limit_manager import limit_manager
from app.pdf_generator import pdf_generator

logger = logging.getLogger(__name__)


class TaskManager:
    """Менеджер обработки задач транскрибации (файл из Telegram или URL)."""

    async def process_transcription_task(
        self,
        update,
        context,
        file_type: str,
        url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Основная задача для обработки медиафайла или ссылки.

        Returns:
            Dict со структурой:
            {
                'success': bool,
                'text': str,
                'segments': list[{'id','start','end','text'}],
                'duration': int,
                'user_id': int,
                'file_type': str,
                'is_url': bool,
                'pdf_path': Optional[str],
                'title': str
            }
            либо {'success': False, 'error': '...'}
        """
        user = update.effective_user
        user_id = user.id

        file_info: Optional[Dict[str, Any]] = None
        wav_path: Optional[str] = None

        try:
            # 1) Загрузка источника
            if url:
                file_info = await download_manager.download_from_url(url)
                if not file_info:
                    return {'success': False, 'error': 'download_failed', 'is_url': True}
            else:
                file_info = await download_manager.download_file(update, context, file_type)
                if not file_info:
                    return {'success': False, 'error': 'download_failed', 'is_url': False}

            # 2) Проверка лимитов
            ok, error_message, _remaining_after = limit_manager.can_process(
                user_id, file_info['duration_seconds']
            )
            if not ok:
                return {'success': False, 'error': 'limit_exceeded', 'message': error_message}

            # 3) Конвертация в WAV (моно 16кHz) при необходимости
            src_path = file_info['file_path']
            if not src_path.lower().endswith('.wav'):
                wav_path = src_path + '.wav'
                if not download_manager.convert_to_wav(src_path, wav_path):
                    return {'success': False, 'error': 'conversion_failed'}
            else:
                wav_path = src_path

            # 4) Транскрибация (унифицировано: faster-whisper или openai-whisper)
            transcription_result = audio_processor.transcribe_audio(wav_path)
            transcription_text = audio_processor.format_transcription(transcription_result)
            segments = transcription_result.get('segments') or []

            # 5) Обновление лимитов
            limit_manager.update_usage(user_id, file_info['duration_seconds'])

            # 6) PDF для длинных текстов (можно менять порог)
            pdf_path = None
            if len(transcription_text) > 1000:
                try:
                    pdf_path = wav_path + '.pdf'
                    title = file_info.get('title', 'Транскрибация')
                    ok_pdf = pdf_generator.generate_transcription_pdf(
                        transcription_text, pdf_path, title
                    )
                    if not ok_pdf:
                        pdf_path = None
                except Exception as e:
                    logger.warning(f"PDF генерация не удалась: {e}")
                    pdf_path = None

            # 7) Ответ
            return {
                'success': True,
                'text': transcription_text,
                'segments': segments,
                'duration': file_info['duration_seconds'],
                'user_id': user_id,
                'file_type': file_type,
                'is_url': bool(url),
                'pdf_path': pdf_path if (pdf_path and os.path.exists(pdf_path)) else None,
                'title': file_info.get('title', 'Файл'),
            }

        except Exception as e:
            logger.exception("Ошибка в задаче транскрибации")
            return {'success': False, 'error': str(e)}

        finally:
            # 8) Очистка временных файлов
            try:
                if file_info and file_info.get('file_path'):
                    # если исходник конвертировали — удалим исходный только после успешного создания wav
                    if wav_path and os.path.exists(wav_path) and file_info['file_path'] != wav_path:
                        # удаляем исходник
                        try:
                            os.remove(file_info['file_path'])
                        except Exception as _e:
                            logger.debug(f"Не удалось удалить исходный файл: {_e}")
                    else:
                        # если не конвертировали — ничего дополнительного не делаем
                        pass

                # удаляем wav, если он был создан как временный (после отправки файлов его не нужно хранить)
                if wav_path and os.path.exists(wav_path):
                    try:
                        os.remove(wav_path)
                    except Exception as _e:
                        logger.debug(f"Не удалось удалить WAV: {_e}")
            except Exception as e:
                logger.debug(f"Очистка файлов: не критичная ошибка: {e}")


# Глобальный экземпляр менеджера задач
task_manager = TaskManager()

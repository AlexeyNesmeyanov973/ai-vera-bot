import os
import logging
from typing import Dict, Any
from app.downloaders import download_manager
from app.audio_processor import audio_processor
from app.limit_manager import limit_manager
from app.pdf_generator import pdf_generator

logger = logging.getLogger(__name__)

class TaskManager:
    """Менеджер для обработки задач транскрибации."""

    async def process_transcription_task(self, update, context, file_type: str, url: str = None) -> Dict[str, Any]:
        """
        Основная задача для обработки медиафайла или ссылки.
        Возвращает словарь результата:
        {
          success: bool,
          error: str|None,
          message: str|None,
          text: str|None,
          duration: int|None,
          user_id: int,
          file_type: str,
          is_url: bool,
          pdf_path: str|None,
          title: str,
          overage_minutes: int,
          overage_cost: float
        }
        """
        user = update.effective_user
        user_id = user.id

        file_info = None
        wav_path = None

        try:
            # 1) Скачать / подготовить вход
            if url:
                file_info = await download_manager.download_from_url(url)
                if not file_info:
                    return {'success': False, 'error': 'download_failed', 'is_url': True, 'user_id': user_id, 'file_type': file_type}
            else:
                file_info = await download_manager.download_file(update, context, file_type)
                if not file_info:
                    return {'success': False, 'error': 'download_failed', 'is_url': False, 'user_id': user_id, 'file_type': file_type}

            duration_s = int(file_info.get('duration_seconds') or 0)

            # 2) Проверить лимиты (+ возможная докупка)
            check = limit_manager.can_process(user_id, duration_s)
            # Поддерживаем старый (3 значения) и новый (5 значений) формат
            over_minutes = 0
            over_cost = 0.0
            if isinstance(check, (tuple, list)) and len(check) >= 3:
                ok, err_msg, remaining = check[0], check[1], check[2]
                if len(check) >= 5:
                    over_minutes = int(check[3] or 0)
                    over_cost = float(check[4] or 0.0)
            else:
                ok, err_msg, remaining = False, "Internal limits error", 0

            if not ok and over_minutes <= 0:
                # полностью упёрлись в лимит, докупки нет
                download_manager.cleanup_file(file_info['file_path'])
                return {'success': False, 'error': 'limit_exceeded', 'message': err_msg, 'user_id': user_id, 'file_type': file_type, 'is_url': bool(url)}

            # 3) Конвертировать в WAV при необходимости
            if not file_info['file_path'].endswith('.wav'):
                wav_path = file_info['file_path'] + '.wav'
                if not download_manager.convert_to_wav(file_info['file_path'], wav_path):
                    download_manager.cleanup_file(file_info['file_path'])
                    return {'success': False, 'error': 'conversion_failed', 'user_id': user_id, 'file_type': file_type, 'is_url': bool(url)}
            else:
                wav_path = file_info['file_path']

            # 4) Транскрибировать
            transcription_result = audio_processor.transcribe_audio(wav_path)

            # 5) Списать фактическое использование (и учесть овередж)
            limit_manager.update_usage(user_id, duration_s, overage_minutes=over_minutes, overage_cost=over_cost)

            # 6) Сформировать текст
            transcription_text = audio_processor.format_transcription(transcription_result)

            # 7) PDF по длинным текстам
            pdf_path = None
            if len(transcription_text) > 1000:
                pdf_path = wav_path + '.pdf'
                title = file_info.get('title', 'Транскрибация')
                pdf_generator.generate_transcription_pdf(transcription_text, pdf_path, title)

            result = {
                'success': True,
                'text': transcription_text,
                'duration': duration_s,
                'user_id': user_id,
                'file_type': file_type,
                'is_url': bool(url),
                'pdf_path': pdf_path if pdf_path and os.path.exists(pdf_path) else None,
                'title': file_info.get('title', 'Файл'),
                'overage_minutes': over_minutes,
                'overage_cost': over_cost
            }
            return result

        except Exception as e:
            logger.error("Ошибка в задаче транскрибации", exc_info=True)
            return {'success': False, 'error': str(e), 'user_id': user_id, 'file_type': file_type, 'is_url': bool(url)}
        finally:
            # очистка
            try:
                if file_info and file_info.get('file_path'):
                    download_manager.cleanup_file(file_info['file_path'])
                if wav_path and os.path.exists(wav_path) and (not file_info or wav_path != file_info.get('file_path')):
                    download_manager.cleanup_file(wav_path)
            except Exception:
                pass


# Глобальный экземпляр
task_manager = TaskManager()

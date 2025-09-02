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
        """
        user = update.effective_user
        user_id = user.id
        
        try:
            file_info = None
            wav_path = None
            
            if url:
                # Обработка ссылки
                file_info = await download_manager.download_from_url(url)
                if not file_info:
                    return {'success': False, 'error': 'download_failed', 'is_url': True}
            else:
                # Обработка файла из Telegram
                file_info = await download_manager.download_file(update, context, file_type)
                if not file_info:
                    return {'success': False, 'error': 'download_failed', 'is_url': False}
            
            # Проверяем лимиты
            can_process, error_message, _ = limit_manager.can_process(user_id, file_info['duration_seconds'])
            
            if not can_process:
                download_manager.cleanup_file(file_info['file_path'])
                return {'success': False, 'error': 'limit_exceeded', 'message': error_message}
            
            # Конвертируем в WAV если нужно
            if not file_info['file_path'].endswith('.wav'):
                wav_path = file_info['file_path'] + '.wav'
                if not download_manager.convert_to_wav(file_info['file_path'], wav_path):
                    download_manager.cleanup_file(file_info['file_path'])
                    return {'success': False, 'error': 'conversion_failed'}
            else:
                wav_path = file_info['file_path']
            
            # Транскрибируем
            transcription_result = audio_processor.transcribe_audio(wav_path)
            
            # Обновляем лимиты
            limit_manager.update_usage(user_id, file_info['duration_seconds'])
            
            # Форматируем результат
            transcription_text = audio_processor.format_transcription(transcription_result)
            
            # Генерируем PDF
            pdf_path = None
            if len(transcription_text) > 1000:  # Генерируем PDF для длинных текстов
                pdf_path = wav_path + '.pdf'
                title = file_info.get('title', 'Транскрибация')
                pdf_generator.generate_transcription_pdf(transcription_text, pdf_path, title)
            
            # Подготавливаем результат
            result = {
                'success': True,
                'text': transcription_text,
                'duration': file_info['duration_seconds'],
                'user_id': user_id,
                'file_type': file_type,
                'is_url': bool(url),
                'pdf_path': pdf_path if pdf_path and os.path.exists(pdf_path) else None,
                'title': file_info.get('title', 'Файл')
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка в задаче транскрибации: {e}")
            return {'success': False, 'error': str(e)}
        
        finally:
            # Всегда очищаем временные файлы
            if 'file_info' in locals() and file_info:
                download_manager.cleanup_file(file_info['file_path'])
            if wav_path and os.path.exists(wav_path) and wav_path != file_info.get('file_path'):
                download_manager.cleanup_file(wav_path)

# Глобальный экземпляр менеджера задач
task_manager = TaskManager()
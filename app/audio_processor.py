import os
import logging
import whisper
from app.config import WHISPER_MODEL

logger = logging.getLogger(__name__)

class AudioProcessor:
    """Класс для транскрибации аудио с помощью Whisper."""
    
    def __init__(self):
        self.model = None
        self.model_name = WHISPER_MODEL
    
    def load_model(self):
        """Загружает модель Whisper."""
        if self.model is None:
            logger.info(f"Загрузка модели Whisper: {self.model_name}")
            self.model = whisper.load_model(self.model_name)
            logger.info("Модель Whisper успешно загружена")
    
    def transcribe_audio(self, audio_path: str) -> dict:
        """
        Транскрибирует аудиофайл с помощью Whisper.
        
        Args:
            audio_path: Путь к аудиофайлу.
            
        Returns:
            dict: Результат транскрибации.
        """
        try:
            self.load_model()
            result = self.model.transcribe(audio_path, language='ru', verbose=False)
            return result
        except Exception as e:
            logger.error(f"Ошибка транскрибации: {e}")
            raise
    
    def format_transcription(self, result: dict, with_timestamps: bool = False) -> str:
        """
        Форматирует результат транскрибации в текст.
        
        Args:
            result: Результат от Whisper.
            with_timestamps: Добавлять ли временные метки.
            
        Returns:
            str: Отформатированный текст.
        """
        if not result or 'text' not in result:
            return "Не удалось распознать текст."
        
        if not with_timestamps:
            return result['text'].strip()
        
        # Форматирование с таймстепми
        formatted_text = ""
        for segment in result.get('segments', []):
            start = segment['start']
            end = segment['end']
            text = segment['text'].strip()
            formatted_text += f"[{start:.0f}s-{end:.0f}s] {text}\n"
        
        return formatted_text.strip()

# Глобальный экземпляр процессора
audio_processor = AudioProcessor()
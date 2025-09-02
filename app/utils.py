import os
import logging
from datetime import timedelta
from pydub import AudioSegment

logger = logging.getLogger(__name__)

def get_audio_duration(file_path: str) -> int:
    """
    Определяет длительность аудиофайла в секундах.
    
    Args:
        file_path: Путь к аудиофайлу.
        
    Returns:
        int: Длительность в секундах.
    """
    try:
        audio = AudioSegment.from_file(file_path)
        return len(audio) // 1000  # Конвертируем миллисекунды в секунды
    except Exception as e:
        logger.error(f"Ошибка при определении длительности аудио {file_path}: {e}")
        raise

def format_seconds(seconds: int) -> str:
    """
    Форматирует секунды в читаемый вид (ЧЧ:ММ:СС).
    
    Args:
        seconds: Количество секунд.
        
    Returns:
        str: Отформатированное время.
    """
    return str(timedelta(seconds=seconds))

def is_audio_file(filename: str) -> bool:
    """
    Проверяет, является ли файл аудиофайлом по расширению.
    
    Args:
        filename: Имя файла.
        
    Returns:
        bool: True если это аудиофайл.
    """
    audio_extensions = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac'}
    return os.path.splitext(filename)[1].lower() in audio_extensions

def is_video_file(filename: str) -> bool:
    """
    Проверяет, является ли файл видеофайлом по расширению.
    
    Args:
        filename: Имя файла.
        
    Returns:
        bool: True если это видеофайл.
    """
    video_extensions = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv'}
    return os.path.splitext(filename)[1].lower() in video_extensions

def get_file_size_mb(file_path: str) -> float:
    """
    Получает размер файла в мегабайтах.
    
    Args:
        file_path: Путь к файлу.
        
    Returns:
        float: Размер файла в МБ.
    """
    return os.path.getsize(file_path) / (1024 * 1024)
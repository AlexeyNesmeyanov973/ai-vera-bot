# app/utils.py
import os
import logging
import subprocess
from datetime import timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _ffprobe_duration_seconds(file_path: str) -> Optional[float]:
    """
    Возвращает длительность через ffprobe (если доступен).
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nokey=1:noprint_wrappers=1",
            file_path,
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
        if out:
            return float(out)
    except Exception:
        return None
    return None


def _pydub_duration_seconds(file_path: str) -> Optional[float]:
    try:
        from pydub import AudioSegment  # импорт лениво, чтобы не ругаться без ffmpeg
        audio = AudioSegment.from_file(file_path)
        return float(len(audio)) / 1000.0
    except Exception as e:
        logger.warning("pydub не смог определить длительность '%s': %s", file_path, e)
        return None


def get_audio_duration(file_path: str) -> int:
    """
    Определяет длительность аудио/видео в секундах (целое),
    сначала через ffprobe, затем через pydub.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    dur = _ffprobe_duration_seconds(file_path)
    if dur is None:
        dur = _pydub_duration_seconds(file_path)
    if dur is None:
        raise RuntimeError(f"Не удалось определить длительность файла: {file_path}")
    return int(round(dur))


def format_seconds(seconds: int) -> str:
    """Формат ЧЧ:ММ:СС."""
    return str(timedelta(seconds=max(0, int(seconds))))


def is_audio_file(filename: str) -> bool:
    audio_extensions = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".webm"}
    return os.path.splitext(filename)[1].lower() in audio_extensions


def is_video_file(filename: str) -> bool:
    video_extensions = {".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".webm"}
    return os.path.splitext(filename)[1].lower() in video_extensions


def get_file_size_mb(file_path: str) -> float:
    return os.path.getsize(file_path) / (1024 * 1024)

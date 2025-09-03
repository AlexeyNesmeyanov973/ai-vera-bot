# app/downloaders.py
import os
import re
import math
import shutil
import logging
import subprocess
from dataclasses import dataclass
from typing import Dict, Any, Optional

import requests

from app.utils import get_audio_duration, get_file_size_mb

logger = logging.getLogger(__name__)

# ===== Настройки через ENV =====
TMP_DIR = os.getenv("TMP_DIR", "downloads")
os.makedirs(TMP_DIR, exist_ok=True)

# лимиты
MAX_TG_FILE_MB = float(os.getenv("MAX_TG_FILE_MB", "20"))        # телеграм файлы
MAX_URL_MB     = float(os.getenv("MAX_URL_MB", "2000"))           # ссылки: по умолчанию 2 ГБ
SOFT_URL_MB    = float(os.getenv("SOFT_URL_MB", "2000"))          # «мягкий» лимит для HEAD/контроля при stream

# потоковая закачка
STREAM_CHUNK_MB  = float(os.getenv("STREAM_CHUNK_MB", "4"))       # размер чанка при stream, МБ
STREAM_TIMEOUT_S = float(os.getenv("STREAM_TIMEOUT_S", "45"))     # таймаут запроса
RESUME_DOWNLOADS = os.getenv("RESUME_DOWNLOADS", "1") == "1"      # попытка докачки (Range)

# yt-dlp: тянуть bestaudio (быстрее) вместо полного видео
YTDLP_AUDIO_ONLY = os.getenv("YTDLP_AUDIO_ONLY", "1") == "1"

YOUTUBE_RX = re.compile(r"(youtube\.com|youtu\.be)", re.I)
VK_RX      = re.compile(r"(vk\.com|vkvideo\.ru)", re.I)

@dataclass
class FileInfo:
    file_path: str
    duration_seconds: int
    title: str = "Файл"


class DownloadManager:
    # ----------------------------- TG файлы -----------------------------

    async def download_file(self, update, context, file_type: str) -> Optional[Dict[str, Any]]:
        """
        Скачивает медиа из Telegram. Возвращает:
        {'file_path': str, 'duration_seconds': int, 'title': str}
        """
        msg = update.message
        file_obj = None
        title = "Файл"

        try:
            if file_type == "voice" and msg.voice:
                file_obj = msg.voice.get_file()
                title = (msg.voice.file_name or msg.voice.file_unique_id or "voice.ogg")
            elif file_type == "audio" and msg.audio:
                file_obj = msg.audio.get_file()
                title = (msg.audio.file_name or msg.audio.file_unique_id or "audio")
            elif file_type == "video" and msg.video:
                file_obj = msg.video.get_file()
                title = (msg.video.file_name or msg.video.file_unique_id or "video")
            elif file_type == "video_note" and msg.video_note:
                file_obj = msg.video_note.get_file()
                title = (msg.video_note.file_unique_id or "videonote") + ".mp4"
            elif file_type == "document" and msg.document:
                file_obj = msg.document.get_file()
                title = (msg.document.file_name or msg.document.file_unique_id or "document")
            else:
                return None

            size_mb = (file_obj.file_size or 0) / (1024 * 1024)
            if size_mb > MAX_TG_FILE_MB:
                # больше лимита — пусть бот подсказку даст про «пришлите ссылку»
                return None

            dst = os.path.join(TMP_DIR, self._safe_name(title))
            await file_obj.download_to_drive(dst)

            duration = self._probe_duration(dst)
            return {"file_path": dst, "duration_seconds": duration, "title": os.path.basename(dst)}

        except Exception:
            logger.exception("TG download error")
            return None

    # --------------------------- URL / ссылки ---------------------------

    async def download_from_url(self, url: str, preferred_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Скачивает по ссылке. Для YouTube — bestaudio; прочие — потоковая закачка на диск.
        preferred_name — желаемое имя результирующего файла на диске (безопасно нормализуется).
        """
        try:
            if YOUTUBE_RX.search(url):
                return self._download_youtube_audio(url, preferred_name)

            if VK_RX.search(url):
                logger.warning("Неподдерживаемый URL (VK): %s", url)
                return None

            return self._download_stream(url, preferred_name)

        except Exception:
            logger.exception("URL download error")
            return None

    # ---------------------------- Конверсия -----------------------------

    def convert_to_wav(self, src_path: str, dst_wav_path: str) -> bool:
        """
        Быстрая конверсия в WAV 16k mono, только если нужно.
        """
        try:
            if src_path.lower().endswith(".wav"):
                shutil.copyfile(src_path, dst_wav_path)
                return True

            cmd = [
                "ffmpeg", "-nostdin", "-v", "error", "-y",
                "-i", src_path,
                "-vn",
                "-ac", "1",
                "-ar", "16000",
                "-acodec", "pcm_s16le",
                dst_wav_path,
            ]
            rc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if rc.returncode != 0:
                logger.error("ffmpeg conversion failed: %s", rc.stderr.decode("utf-8", "ignore"))
                return False
            return True
        except Exception:
            logger.exception("convert_to_wav error")
            return False

    # ----------------------------- Утилиты -----------------------------

    def cleanup_file(self, path: str):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    # --------------------------- Внутреннее ----------------------------

    def _download_youtube_audio(self, url: str, preferred_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        yt-dlp bestaudio, чтобы быстрее начать распознавание.
        """
        try:
            from yt_dlp import YoutubeDL
        except Exception as e:
            logger.error("yt-dlp unavailable: %s", e)
            return None

        safe_name = self._safe_name(preferred_name) if preferred_name else None
        outtmpl = os.path.join(TMP_DIR, (safe_name or "yt_%(id)s")) + ".%(ext)s"

        ydl_opts = {
            "format": "bestaudio/best" if YTDLP_AUDIO_ONLY else "best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"}
            ],
            "nocheckcertificate": True,
            "retries": 3,
            "fragment_retries": 3,
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded = ydl.prepare_filename(info)
            base, _ = os.path.splitext(downloaded)
            candidates = [base + ".m4a", base + ".mp3", downloaded]
            file_path = next((p for p in candidates if os.path.exists(p)), None)
            if not file_path:
                logger.error("yt-dlp: файл не найден после скачивания")
                return None

            if get_file_size_mb(file_path) > MAX_URL_MB:
                self.cleanup_file(file_path)
                return None

            title = (info.get("title") or "YouTube аудио").strip()
            duration = self._probe_duration(file_path)
            return {"file_path": file_path, "duration_seconds": duration, "title": title}

    def _download_stream(self, url: str, preferred_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Стримовая загрузка «как есть» (до 2 ГБ и больше — если поднимете MAX_URL_MB).
        Поддерживает докачку (Range) при RESUME_DOWNLOADS=1.
        """
        # имя файла
        name_from_url = os.path.basename(url.split("?", 1)[0]) or "download.bin"
        safe = self._safe_name(preferred_name or name_from_url)
        dst_path = os.path.join(TMP_DIR, safe)

        # HEAD — чтобы понять примерный размер (если сервер даёт)
        total_size = None
        try:
            with requests.head(url, allow_redirects=True, timeout=15) as r:
                cl = r.headers.get("Content-Length")
                if cl and cl.isdigit():
                    total_size = int(cl)
                    if (total_size / (1024 * 1024)) > SOFT_URL_MB:
                        logger.warning("Файл по HEAD больше лимита: %.1f МБ", total_size / (1024 * 1024))
                        return None
        except Exception:
            # не критично
            pass

        mode = "wb"
        headers = {}
        downloaded_bytes = 0

        if RESUME_DOWNLOADS and os.path.exists(dst_path):
            downloaded_bytes = os.path.getsize(dst_path)
            if downloaded_bytes > 0:
                headers["Range"] = f"bytes={downloaded_bytes}-"
                mode = "ab"

        chunk_size = int(STREAM_CHUNK_MB * 1024 * 1024)

        try:
            with requests.get(url, headers=headers, stream=True, timeout=STREAM_TIMEOUT_S) as r:
                r.raise_for_status()
                # если сервер поддержал Range — докачка; если нет — начинаем с нуля
                if r.status_code == 200 and "Range" in headers and downloaded_bytes:
                    # сервер игнорировал Range → перезаписываем
                    downloaded_bytes = 0
                    mode = "wb"

                with open(dst_path, mode) as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                        # жёсткий лимит — не больше MAX_URL_MB
                        if downloaded_bytes > MAX_URL_MB * 1024 * 1024:
                            raise RuntimeError("file too large")

        except Exception as e:
            logger.warning("stream download failed: %s", e)
            # оборвать частичник только если совсем крошечный; иначе оставим для повторной докачки
            if os.path.exists(dst_path) and os.path.getsize(dst_path) < 256 * 1024:
                os.remove(dst_path)
            return None

        duration = self._probe_duration(dst_path)
        return {"file_path": dst_path, "duration_seconds": duration, "title": os.path.basename(dst_path)}

    def _probe_duration(self, path: str) -> int:
        try:
            return int(get_audio_duration(path))
        except Exception:
            return 0

    def _safe_name(self, name: str) -> str:
        safe = "".join(c for c in (name or "") if c.isalnum() or c in " ._-").strip()
        return safe or "file"


# Экземпляр менеджера
download_manager = DownloadManager()

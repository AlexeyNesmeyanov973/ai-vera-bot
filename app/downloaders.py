# app/downloaders.py
# app/downloaders.py
import asyncio
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass
from typing import Optional, Dict, Any

import aiohttp

from app.config import (
    TMP_DIR,
    STREAM_CHUNK_MB,
    STREAM_TIMEOUT_S,
    RESUME_DOWNLOADS,
    YTDLP_AUDIO_ONLY,
)

logger = logging.getLogger(__name__)

_YTDLP_IMPORTED = False
try:
    import yt_dlp  # type: ignore
    _YTDLP_IMPORTED = True
except Exception:
    logger.warning("yt-dlp недоступен — часть ссылок может не скачаться")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI-Vera/1.0; +https://t.me/)",
}

DIRECT_FILE_EXT = (
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac",
    ".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".webm",
)

YTDLP_DOMAINS = (
    "youtube.com", "youtu.be",
    "vimeo.com",
    "soundcloud.com",
    "tiktok.com",
)

def _safe_name(prefix="media") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

def _is_probably_direct(url: str) -> bool:
    low = url.lower()
    if any(ext in low for ext in DIRECT_FILE_EXT):
        return True
    return False

async def _download_direct_stream(
    url: str,
    dest_dir: str,
    max_size_mb: float,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Потоковая докачка напрямую по URL (HTTP GET).
    Ограничивает общий размер max_size_mb.
    """
    os.makedirs(dest_dir, exist_ok=True)
    fname = _safe_name("download")
    # Попробуем угадать расширение
    ext = ".bin"
    for e in DIRECT_FILE_EXT:
        if url.lower().split("?")[0].endswith(e):
            ext = e
            break
    out_path = os.path.join(dest_dir, fname + ext)

    chunk_size = int(STREAM_CHUNK_MB * 1024 * 1024)
    timeout = aiohttp.ClientTimeout(total=None, sock_read=STREAM_TIMEOUT_S)
    total = 0

    async with aiohttp.ClientSession(timeout=timeout, headers=headers or DEFAULT_HEADERS) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return {"success": False, "error": f"HTTP {resp.status}"}
            # name from headers
            cd = resp.headers.get("Content-Disposition", "")
            m = re.search(r'filename="?([^"]+)"?', cd)
            if m:
                out_name = m.group(1)
                out_path = os.path.join(dest_dir, out_name)

            with open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(chunk_size):
                    if not chunk:
                        continue
                    f.write(chunk)
                    total += len(chunk)
                    if total > max_size_mb * 1024 * 1024:
                        try:
                            f.close()
                            os.remove(out_path)
                        except Exception:
                            pass
                        return {"success": False, "error": f"Файл больше {max_size_mb} МБ"}

    size_mb = total / (1024 * 1024)
    return {
        "success": True,
        "path": out_path,
        "file_size_mb": size_mb,
        "title": os.path.basename(out_path),
        "duration": 0.0,  # уточнится после транскриба
    }

async def _download_with_ytdlp(
    url: str,
    dest_dir: str,
    max_size_mb: float,
) -> Dict[str, Any]:
    """
    yt-dlp для платформ (YouTube, Vimeo, и т.д.).
    Сохраняем как лучший аудио-поток (или контейнер).
    """
    if not _YTDLP_IMPORTED:
        return {"success": False, "error": "yt-dlp не установлен"}

    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.join(dest_dir, _safe_name("ytdlp"))
    # Формат — аудио-лучший (уменьшает размер)
    ytfmt = "bestaudio/best" if str(YTDLP_AUDIO_ONLY) == "1" else "best"

    # Ограничение размера реализуем через postprocessor args (не всегда возможно),
    # поэтому делаем мягко: качаем, а потом проверяем вес.
    ydl_opts = {
        "outtmpl": base + ".%(ext)s",
        "format": ytfmt,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "http_headers": DEFAULT_HEADERS,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
        info = ydl.extract_info(url, download=True)
        out_path = ydl.prepare_filename(info)
        try:
            # иногда добавляется ".webm" позже — найдём реальный файл
            if not os.path.exists(out_path):
                # поиск ближайшего
                for f in os.listdir(dest_dir):
                    if os.path.basename(out_path).split(".")[0] in f:
                        out_path = os.path.join(dest_dir, f)
                        break
        except Exception:
            pass

    if not os.path.exists(out_path):
        return {"success": False, "error": "Не удалось сохранить файл yt-dlp"}

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    if size_mb > max_size_mb:
        try:
            os.remove(out_path)
        except Exception:
            pass
        return {"success": False, "error": f"Файл больше {max_size_mb} МБ"}

    title = info.get("title") or os.path.basename(out_path)
    duration = float(info.get("duration") or 0.0)
    return {
        "success": True,
        "path": out_path,
        "file_size_mb": size_mb,
        "title": title,
        "duration": duration,
    }

# ---------- Публичные функции ----------

async def download_from_url(url: str, dest_dir: str, max_size_mb: float) -> Dict[str, Any]:
    """
    Универсальная загрузка по URL:
      • если похоже на прямой файл — качаем потоково (aiohttp)
      • иначе — пробуем yt-dlp
    """
    # маленький sanity-check
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"success": False, "error": "Некорректный URL"}

    try:
        if _is_probably_direct(url):
            return await _download_direct_stream(url, dest_dir, max_size_mb)
        # Проверим домены, где у yt-dlp больше шансов
        if any(d in url for d in YTDLP_DOMAINS):
            return await _download_with_ytdlp(url, dest_dir, max_size_mb)
        # Попытаться как прямой файл
        res = await _download_direct_stream(url, dest_dir, max_size_mb)
        if res.get("success"):
            return res
        # Фолбэк в yt-dlp
        return await _download_with_ytdlp(url, dest_dir, max_size_mb)
    except Exception as e:
        logger.exception("download_from_url error")
        return {"success": False, "error": str(e)}

async def download_from_telegram(update, context, file_type: str, dest_dir: str, max_size_mb: float) -> Dict[str, Any]:
    """
    Скачивание из Telegram. Предполагается, что размер уже проверен в боте,
    но тут тоже ограничим на всякий случай.
    """
    os.makedirs(dest_dir, exist_ok=True)
    msg = update.message

    tg_file = None
    title = None
    size_bytes = 0

    # Получаем объект файла
    if file_type == "voice" and msg.voice:
        tg_file = await msg.voice.get_file()
        size_bytes = msg.voice.file_size or 0
        title = "voice.ogg"
    elif file_type == "audio" and msg.audio:
        tg_file = await msg.audio.get_file()
        size_bytes = msg.audio.file_size or 0
        title = msg.audio.file_name or "audio"
    elif file_type == "video" and msg.video:
        tg_file = await msg.video.get_file()
        size_bytes = msg.video.file_size or 0
        title = msg.video.file_name or "video"
    elif file_type == "video_note" and msg.video_note:
        tg_file = await msg.video_note.get_file()
        size_bytes = msg.video_note.file_size or 0
        title = "video_note.mp4"
    elif file_type == "document" and msg.document:
        tg_file = await msg.document.get_file()
        size_bytes = msg.document.file_size or 0
        title = msg.document.file_name or "document"
    else:
        return {"success": False, "error": "Не найден файл в сообщении"}

    size_mb = size_bytes / (1024 * 1024)
    if size_mb > max_size_mb:
        return {"success": False, "error": f"Файл больше {max_size_mb} МБ"}

    # Сохраняем
    safe = title if "." in title else f"{title}.bin"
    out_path = os.path.join(dest_dir, f"{uuid.uuid4().hex[:8]}_{safe}")
    await tg_file.download_to_drive(custom_path=out_path)

    # duration для аудио/видео из Telegram (если телега прислала)
    duration = 0.0
    if file_type == "audio" and msg.audio and msg.audio.duration:
        duration = float(msg.audio.duration)
    elif file_type == "video" and msg.video and msg.video.duration:
        duration = float(msg.video.duration)
    elif file_type == "video_note" and msg.video_note and msg.video_note.duration:
        duration = float(msg.video_note.duration)
    elif file_type == "voice" and msg.voice and msg.voice.duration:
        duration = float(msg.voice.duration)

    return {
        "success": True,
        "path": out_path,
        "file_size_mb": size_mb,
        "title": title,
        "duration": duration,
    }

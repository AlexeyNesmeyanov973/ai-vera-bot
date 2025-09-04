# app/downloaders.py
import asyncio
import logging
import os
import re
import uuid
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

_CONTENT_TYPE_TO_EXT = {
    "audio/mpeg": ".mp3",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


def _safe_name(prefix: str = "media") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _is_probably_direct(url: str) -> bool:
    low = url.lower()
    return any(low.split("?", 1)[0].endswith(ext) for ext in DIRECT_FILE_EXT)


def _sanitize_filename(name: str) -> str:
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\r\n\t]", "_", name)
    return name.strip() or _safe_name("download")


async def _download_direct_stream(
    url: str,
    dest_dir: str,
    max_size_mb: float,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Потоковая докачка напрямую по URL (HTTP GET) с ограничением на общий размер.
    """
    os.makedirs(dest_dir, exist_ok=True)
    fname = _safe_name("download")
    out_path = os.path.join(dest_dir, fname + ".bin")

    chunk_size = max(1, int(STREAM_CHUNK_MB * 1024 * 1024))
    timeout = aiohttp.ClientTimeout(total=None, sock_read=STREAM_TIMEOUT_S)
    total = 0

    # Пул коннектов ограничим немного, чтобы не «забить» контейнер
    connector = aiohttp.TCPConnector(limit=8)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers or DEFAULT_HEADERS, connector=connector) as session:
        # HEAD — попробуем заранее понять размер (не все сервера поддерживают)
        try:
            async with session.head(url, allow_redirects=True) as hresp:
                if hresp.status // 100 == 2:
                    cl = hresp.headers.get("Content-Length")
                    if cl and cl.isdigit():
                        size_mb = int(cl) / (1024 * 1024)
                        if size_mb > max_size_mb:
                            return {"success": False, "error": f"Файл больше {max_size_mb} МБ"}
        except Exception:
            pass

        async with session.get(url) as resp:
            if resp.status != 200:
                return {"success": False, "error": f"HTTP {resp.status}"}

            # Имя файла
            cd = resp.headers.get("Content-Disposition", "")
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.IGNORECASE)
            if m:
                out_name = _sanitize_filename(m.group(1))
                out_path = os.path.join(dest_dir, out_name)

            # Расширение по content-type, если не смогли угадать
            ctype = resp.headers.get("Content-Type", "").split(";")[0].lower()
            if out_path.endswith(".bin") and ctype in _CONTENT_TYPE_TO_EXT:
                out_path = out_path[:-4] + _CONTENT_TYPE_TO_EXT[ctype]

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
        "duration": 0.0,  # уточним позднее
    }


async def _download_with_ytdlp(
    url: str,
    dest_dir: str,
    max_size_mb: float,
) -> Dict[str, Any]:
    """
    yt-dlp для платформ (YouTube, Vimeo, SoundCloud, TikTok…).
    """
    if not _YTDLP_IMPORTED:
        return {"success": False, "error": "yt-dlp не установлен"}

    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.join(dest_dir, _safe_name("ytdlp"))
    ytfmt = "bestaudio/best" if str(YTDLP_AUDIO_ONLY) in ("1", "true", "yes") else "best"

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
        if not os.path.exists(out_path):
            # Попробуем подобрать итоговый файл, если расширение изменилось
            stem = os.path.basename(out_path).rsplit(".", 1)[0]
            for f in os.listdir(dest_dir):
                if f.startswith(stem + "."):
                    out_path = os.path.join(dest_dir, f)
                    break

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
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"success": False, "error": "Некорректный URL"}

    try:
        if _is_probably_direct(url):
            return await _download_direct_stream(url, dest_dir, max_size_mb)

        if any(d in url for d in YTDLP_DOMAINS):
            return await _download_with_ytdlp(url, dest_dir, max_size_mb)

        # 1) пробуем как прямой файл
        res = await _download_direct_stream(url, dest_dir, max_size_mb)
        if res.get("success"):
            return res
        # 2) фолбэк в yt-dlp
        return await _download_with_ytdlp(url, dest_dir, max_size_mb)
    except Exception as e:
        logger.exception("download_from_url error")
        return {"success": False, "error": str(e)}


async def download_from_telegram(update, context, file_type: str, dest_dir: str, max_size_mb: float) -> Dict[str, Any]:
    """
    Скачивание из Telegram. Размер уже проверен в боте, но дубль-проверка тут тоже есть.
    """
    os.makedirs(dest_dir, exist_ok=True)
    msg = update.message

    tg_file = None
    title = None
    size_bytes = 0

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

    safe = title if "." in (title or "") else f"{title}.bin"
    out_path = os.path.join(dest_dir, f"{uuid.uuid4().hex[:8]}_{safe}")
    await tg_file.download_to_drive(custom_path=out_path)

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

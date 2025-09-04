# app/downloaders.py
import asyncio
import hashlib
import json
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
    "Accept": "*/*",
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

_MAX_REDIRECTS = 5
_RESUME_RETRIES = 3


def _safe_name(prefix: str = "media") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _is_probably_direct(url: str) -> bool:
    low = url.lower()
    return any(low.split("?", 1)[0].endswith(ext) for ext in DIRECT_FILE_EXT)


def _sanitize_filename(name: str) -> str:
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\r\n\t]", "_", name)
    return name.strip() or _safe_name("download")


def _resume_key(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def _paths_for_url(dest_dir: str, url: str) -> Dict[str, str]:
    """
    Файлы для межпроцессной докачки:
      • .part       — тело файла
      • .meta.json  — sidecar с ETag/Last-Modified/ожидаемым именем и размером
    """
    os.makedirs(dest_dir, exist_ok=True)
    key = _resume_key(url)
    base = os.path.join(dest_dir, f"dl_{key}")
    return {
        "part": base + ".part",
        "meta": base + ".meta.json",
        "final_guess": base + ".bin",
    }


def _load_meta(meta_path: str) -> Dict[str, Any]:
    try:
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        logger.debug("Не удалось прочитать meta: %s", meta_path)
    return {}


def _save_meta(meta_path: str, data: Dict[str, Any]) -> None:
    try:
        tmp = meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, meta_path)
    except Exception:
        logger.debug("Не удалось сохранить meta: %s", meta_path)


def _decide_final_name(url: str, headers: Dict[str, str], fallback_path: str) -> str:
    """
    Возвращает путь с «правильным» именем на основе Content-Disposition/Type и URL.
    """
    # 1) Content-Disposition
    cd = headers.get("Content-Disposition", "") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.IGNORECASE)
    if m:
        fn = _sanitize_filename(m.group(1))
        return os.path.join(os.path.dirname(fallback_path), fn)

    # 2) URL path name
    try:
        tail = url.split("?", 1)[0].rstrip("/").split("/")[-1]
        if tail:
            guess = _sanitize_filename(tail)
            return os.path.join(os.path.dirname(fallback_path), guess)
    except Exception:
        pass

    # 3) fallback
    return fallback_path


def _maybe_adjust_extension(path: str, content_type: str) -> str:
    ext = _CONTENT_TYPE_TO_EXT.get((content_type or "").split(";")[0].lower())
    if not ext:
        return path
    if not path.lower().endswith(ext):
        newp = os.path.splitext(path)[0] + ext
        try:
            if os.path.exists(path):
                os.replace(path, newp)
        except Exception:
            return path
        return newp
    return path


async def _download_direct_stream(
    url: str,
    dest_dir: str,
    max_size_mb: float,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Потоковая загрузка напрямую по URL с:
      • HEAD проверкой (размер, ETag/Last-Modified, Accept-Ranges)
      • межпроцессной докачкой .part + .meta.json
      • Range + If-Range валидацией
    """
    paths = _paths_for_url(dest_dir, url)
    part_path = paths["part"]
    meta_path = paths["meta"]
    final_guess = paths["final_guess"]

    # state
    chunk_size = max(1, int(float(STREAM_CHUNK_MB) * 1024 * 1024))
    timeout = aiohttp.ClientTimeout(
        total=max(5, int(STREAM_TIMEOUT_S) * 4),
        sock_read=int(STREAM_TIMEOUT_S),
    )
    allow_resume = bool(int(RESUME_DOWNLOADS))
    expected_size: Optional[int] = None
    accept_ranges = False
    etag = None
    last_modified = None

    # resume info
    meta = _load_meta(meta_path)
    downloaded = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    total_written = downloaded
    final_path = meta.get("final_name") or final_guess

    connector = aiohttp.TCPConnector(limit=8)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers or DEFAULT_HEADERS,
        connector=connector,
    ) as session:
        # HEAD
        try:
            async with session.head(url, allow_redirects=True, max_redirects=_MAX_REDIRECTS) as h:
                if h.status // 100 == 2:
                    cl = h.headers.get("Content-Length")
                    if cl and cl.isdigit():
                        expected_size = int(cl)
                        if (expected_size / (1024 * 1024)) > max_size_mb:
                            return {"success": False, "error": f"Файл больше {max_size_mb} МБ"}
                    ar = (h.headers.get("Accept-Ranges", "") or "").lower()
                    accept_ranges = ("bytes" in ar) or (ar == "bytes")
                    etag = h.headers.get("ETag")
                    last_modified = h.headers.get("Last-Modified")
        except Exception:
            pass

        # если сервер не поддерживает Range — удалим .part, качаем с нуля
        if downloaded > 0 and not accept_ranges:
            try:
                os.remove(part_path)
            except Exception:
                pass
            downloaded = 0
            total_written = 0

        # основной цикл (с ретраями докачки)
        attempts = 0
        while True:
            req_headers: Dict[str, str] = {}
            mode = "wb"
            if allow_resume and downloaded > 0 and accept_ranges:
                req_headers["Range"] = f"bytes={downloaded}-"
                if etag:
                    req_headers["If-Range"] = etag
                elif last_modified:
                    req_headers["If-Range"] = last_modified
                mode = "ab"

            try:
                resp_ctx = session.get(
                    url,
                    headers=req_headers or None,
                    allow_redirects=True,
                    max_redirects=_MAX_REDIRECTS,
                )
            except Exception as e:
                return {"success": False, "error": f"HTTP init error: {e}"}

            try:
                async with resp_ctx as resp:
                    if resp.status not in (200, 206):
                        return {"success": False, "error": f"HTTP {resp.status}"}

                    # Определим финальное имя (по CD/URL), сохраним в meta
                    final_candidate = _decide_final_name(url, resp.headers, final_path)
                    final_candidate = _maybe_adjust_extension(final_candidate, resp.headers.get("Content-Type", ""))
                    # Если меняем имя, то .part остаётся тем же — переносим уже потом
                    final_path = final_candidate
                    meta.update({
                        "url": url,
                        "etag": resp.headers.get("ETag") or etag,
                        "last_modified": resp.headers.get("Last-Modified") or last_modified,
                        "accept_ranges": accept_ranges,
                        "expected_size": expected_size,
                        "final_name": final_path,
                        "content_type": resp.headers.get("Content-Type"),
                    })
                    _save_meta(meta_path, meta)

                    # Если просили Range, но пришёл 200 — ресурс изменился: начинаем заново
                    if "Range" in req_headers and resp.status == 200:
                        try:
                            if os.path.exists(part_path):
                                os.remove(part_path)
                        except Exception:
                            pass
                        downloaded = 0
                        total_written = 0
                        mode = "wb"

                    # Обновим ожидаемый размер по Content-Range
                    cr = resp.headers.get("Content-Range")
                    if cr:
                        m = re.search(r"/(\d+)$", cr)
                        if m:
                            try:
                                expected_size = int(m.group(1))
                            except Exception:
                                pass

                    # Стримим в .part
                    os.makedirs(os.path.dirname(part_path), exist_ok=True)
                    with open(part_path, mode) as f:
                        async for chunk in resp.content.iter_chunked(chunk_size):
                            if not chunk:
                                continue
                            f.write(chunk)
                            total_written += len(chunk)
                            downloaded += len(chunk)
                            # лимит размера
                            if total_written > max_size_mb * 1024 * 1024:
                                try:
                                    f.close()
                                    os.remove(part_path)
                                except Exception:
                                    pass
                                return {"success": False, "error": f"Файл больше {max_size_mb} МБ"}

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                attempts += 1
                if not allow_resume or attempts > _RESUME_RETRIES:
                    return {"success": False, "error": f"Сетевая ошибка: {e}"}
                # ретрай с Range
                await asyncio.sleep(0.5 * attempts)
                continue

            # проверка полноты
            if expected_size is None or downloaded >= expected_size:
                break

            # недокачали — ещё попытка
            attempts += 1
            if attempts > _RESUME_RETRIES:
                return {"success": False, "error": "Не удалось докачать файл: исчерпаны попытки"}
            await asyncio.sleep(0.5 * attempts)

    # Доименовываем: переносим .part → финальный путь
    try:
        # если финальный файл уже есть — перезапишем
        if os.path.exists(final_path):
            os.remove(final_path)
        os.replace(part_path, final_path)
    except Exception as e:
        logger.error("Переименование .part → финал не удалось: %s", e)
        # fallback — оставим .bin
        final_path = paths["final_guess"]
        try:
            if os.path.exists(final_path):
                os.remove(final_path)
            os.replace(part_path, final_path)
        except Exception:
            return {"success": False, "error": "Не удалось сохранить итоговый файл"}

    size_mb = (os.path.getsize(final_path) if os.path.exists(final_path) else 0) / (1024 * 1024)
    # очистим meta — закачка завершена
    try:
        if os.path.exists(meta_path):
            os.remove(meta_path)
    except Exception:
        pass

    return {
        "success": True,
        "path": final_path,
        "file_size_mb": size_mb,
        "title": os.path.basename(final_path),
        "duration": 0.0,
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
    ytfmt = "bestaudio/best" if str(YTDLP_AUDIO_ONLY).lower() in ("1", "true", "yes", "y", "on") else "best"

    ydl_opts = {
        "outtmpl": base + ".%(ext)s",
        "format": ytfmt,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "http_headers": DEFAULT_HEADERS,
        "socket_timeout": int(STREAM_TIMEOUT_S) or 30,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            info = ydl.extract_info(url, download=True)
            out_path = ydl.prepare_filename(info)
    except Exception as e:
        return {"success": False, "error": f"yt-dlp error: {e}"}

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

        # 1) Пробуем как прямой файл
        res = await _download_direct_stream(url, dest_dir, max_size_mb)
        if res.get("success"):
            return res
        # 2) Фолбэк в yt-dlp
        return await _download_with_ytdlp(url, dest_dir, max_size_mb)

    except aiohttp.TooManyRedirects:
        return {"success": False, "error": f"Слишком много редиректов (>{_MAX_REDIRECTS})"}
    except asyncio.TimeoutError:
        return {"success": False, "error": "Таймаут загрузки"}
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

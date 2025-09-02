import os
import re
import logging
from typing import Optional
from pydub import AudioSegment
from telegram import Update
from app.utils import get_audio_duration, get_file_size_mb
from app.config import MAX_FILE_SIZE_MB
import asyncio
import yt_dlp

logger = logging.getLogger(__name__)

class DownloadManager:
    """Класс для загрузки и обработки медиафайлов из Telegram и по ссылкам."""
    def __init__(self, download_dir: str = "downloads"):
        self.download_dir = download_dir
        os.makedirs(download_dir, exist_ok=True)

    def _is_youtube_url(self, url: str) -> bool:
        youtube_patterns = [
            r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/',
            r'youtube\.com/watch\?v=',
            r'youtu\.be/'
        ]
        return any(re.search(pattern, url) for pattern in youtube_patterns)

    def _is_yandex_disk_url(self, url: str) -> bool:
        return 'yadi.sk' in url or 'disk.yandex.' in url

    def _is_google_drive_url(self, url: str) -> bool:
        return 'drive.google.com' in url

    async def download_from_url(self, url: str) -> Optional[dict]:
        try:
            if self._is_youtube_url(url):
                return await self._download_youtube_video(url)
            elif self._is_yandex_disk_url(url) or self._is_google_drive_url(url):
                return await self._download_cloud_file(url)
            else:
                logger.warning(f"Неподдерживаемый URL: {url}")
                return None
        except Exception as e:
            logger.error(f"Ошибка при загрузке по URL {url}: {e}")
            return None

    async def _download_youtube_video(self, url: str) -> Optional[dict]:
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(self.download_dir, '%(id)s.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'wav',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'no_warnings': True,
            }

            def _work():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    audio_path = ydl.prepare_filename(info).replace('.webm', '.wav').replace('.m4a', '.wav')
                    return info, audio_path

            info, audio_path = await asyncio.to_thread(_work)

            if not os.path.exists(audio_path):
                raise Exception("Не удалось скачать аудио с YouTube")

            duration_seconds = info.get('duration', 0)
            file_size_mb = get_file_size_mb(audio_path)

            if file_size_mb > MAX_FILE_SIZE_MB:
                os.remove(audio_path)
                logger.warning(f"Файл с YouTube превышает лимит: {file_size_mb} МБ")
                return None

            return {
                'file_path': audio_path,
                'file_id': info['id'],
                'duration_seconds': duration_seconds,
                'file_size_mb': file_size_mb,
                'file_type': 'youtube',
                'title': info.get('title', 'YouTube видео')
            }

        except Exception as e:
            logger.error(f"Ошибка загрузки YouTube видео: {e}")
            return None

    async def _download_cloud_file(self, url: str) -> Optional[dict]:
        try:
            ydl_opts = {
                'outtmpl': os.path.join(self.download_dir, '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
            }

            def _work():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    file_path = ydl.prepare_filename(info)
                    return info, file_path

            info, file_path = await asyncio.to_thread(_work)

            if not os.path.exists(file_path):
                raise Exception("Не удалось скачать файл")

            if any(file_path.endswith(ext) for ext in ['.mp3', '.m4a', '.mp4', '.avi', '.mov']):
                wav_path = file_path + '.wav'
                if self.convert_to_wav(file_path, wav_path):
                    os.remove(file_path)
                    file_path = wav_path

            duration_seconds = get_audio_duration(file_path)
            file_size_mb = get_file_size_mb(file_path)

            if file_size_mb > MAX_FILE_SIZE_MB:
                os.remove(file_path)
                logger.warning(f"Файл с облака превышает лимит: {file_size_mb} МБ")
                return None

            return {
                'file_path': file_path,
                'file_id': os.path.basename(file_path),
                'duration_seconds': duration_seconds,
                'file_size_mb': file_size_mb,
                'file_type': 'cloud',
                'title': info.get('title', 'Файл из облака')
            }

        except Exception as e:
            logger.error(f"Ошибка загрузки облачного файла: {e}")
            return None

    async def download_file(self, update: Update, context, file_type: str) -> Optional[dict]:
        try:
            if file_type == 'voice':
                file = update.message.voice
            elif file_type == 'audio':
                file = update.message.audio
            elif file_type == 'video':
                file = update.message.video
            elif file_type == 'video_note':
                file = update.message.video_note
            elif file_type == 'document':
                file = update.message.document
            else:
                await update.message.reply_text("❌ Неподдерживаемый тип файла.")
                return None

            file_obj = await file.get_file()
            file_extension = os.path.splitext(file_obj.file_path)[1] if file_obj.file_path else '.ogg'
            if file_type in ('voice', 'video_note'):
                file_extension = '.ogg'

            download_path = os.path.join(self.download_dir, f"{file.file_id}{file_extension}")
            await file_obj.download_to_drive(download_path)

            file_size_mb = get_file_size_mb(download_path)
            if file_size_mb > MAX_FILE_SIZE_MB:
                os.remove(download_path)
                await update.message.reply_text(
                    f"❌ Размер файла ({file_size_mb:.1f} МБ) превышает максимально допустимый ({MAX_FILE_SIZE_MB} МБ)."
                )
                return None

            duration_seconds = get_audio_duration(download_path)

            return {
                'file_path': download_path,
                'file_id': file.file_id,
                'duration_seconds': duration_seconds,
                'file_size_mb': file_size_mb,
                'file_type': file_type
            }

        except Exception as e:
            logger.error(f"Ошибка при загрузке файла: {e}")
            await update.message.reply_text("❌ Произошла ошибка при загрузке файла. Попробуйте еще раз.")
            return None

    def convert_to_wav(self, input_path: str, output_path: str) -> bool:
        try:
            audio = AudioSegment.from_file(input_path)
            audio.export(output_path, format="wav", parameters=["-ac", "1", "-ar", "16000"])
            return True
        except Exception as e:
            logger.error(f"Ошибка конвертации в WAV: {e}")
            return False

    def cleanup_file(self, file_path: str):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.error(f"Ошибка при удалении файла {file_path}: {e}")

download_manager = DownloadManager()

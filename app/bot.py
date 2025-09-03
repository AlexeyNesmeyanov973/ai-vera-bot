# app/bot.py
import logging
import asyncio
import os
import sys
import uuid
from math import ceil

from telegram import (
    Update,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.error import Conflict  # «мягкая защита» одного polling

from app.config import (
    TELEGRAM_BOT_TOKEN,
    WHISPER_BACKEND,
    WHISPER_MODEL,
    ADMIN_USER_IDS,
    OVERAGE_PRICE_RUB,
    MAX_FILE_SIZE_MB,
    URL_MAX_FILE_SIZE_MB,
)
from app import storage
from app.utils import format_seconds
from app.task_queue import task_queue
from app.task_manager import task_manager
from app.bootstrap import run_startup_migrations
from app.payments_bootstrap import payment_manager
from app.pdf_generator import pdf_generator
from app.translator import translate_text
from app.analytics import analyze_text, build_report_md




# Приглушим шум от httpx (getUpdates каждые N секунд)
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Языки: код -> (Название, Флаг) ---
_LANG_MAP = {
    "ru": ("Русский", "🇷🇺"),
    "en": ("English", "🇬🇧"),   # можно переключить на 🇺🇸, если хочешь
    "uk": ("Українська", "🇺🇦"),
    "de": ("Deutsch", "🇩🇪"),
    "fr": ("Français", "🇫🇷"),
    "es": ("Español", "🇪🇸"),
    "it": ("Italiano", "🇮🇹"),
    "pt": ("Português", "🇵🇹"),
    "pl": ("Polski", "🇵🇱"),
    "tr": ("Türkçe", "🇹🇷"),
    "kk": ("Қазақша", "🇰🇿"),
    "uz": ("Oʻzbekcha", "🇺🇿"),
    "az": ("Azərbaycanca", "🇦🇿"),
    "he": ("עברית", "🇮🇱"),
    "ar": ("العربية", "🇸🇦"),
    "fa": ("فارسی", "🇮🇷"),
    "hi": ("हिन्दी", "🇮🇳"),
    "bn": ("বাংলা", "🇧🇩"),
    "zh": ("中文", "🇨🇳"),
    "ja": ("日本語", "🇯🇵"),
    "ko": ("한국어", "🇰🇷"),
    # добавляй по мере надобности
}

def _lang_pretty(code: str | None) -> str:
    """Возвращает красивую строку вида 'English 🇬🇧 (en)'."""
    if not code:
        return "неизвестен 🌐"
    c = code.lower().strip()
    name, flag = _LANG_MAP.get(c, (c, "🌐"))
    return f"{name} {flag} ({c})"

# Подстраховка, если инстанс не экспортирован (ImportError)
try:
    from app.limit_manager import limit_manager
except ImportError:
    from app.limit_manager import LimitManager
    limit_manager = LimitManager()

# ---------- Вспомогательное меню ----------

def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("⏱ Статус"), KeyboardButton("ℹ️ Помощь")],
            [KeyboardButton("💎 PRO"), KeyboardButton("🔗 Отправить ссылку")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    
def _translation_keyboard() -> InlineKeyboardMarkup:
    options = [
        ("ru", "На русский 🇷🇺"),
        ("en", "На английский 🇬🇧"),
        ("es", "На испанский 🇪🇸"),
        ("de", "На немецкий 🇩🇪"),
    ]
    rows = []
    for i in range(0, len(options), 2):
        row = []
        for code, label in options[i:i+2]:
            row.append(InlineKeyboardButton(f"➡️ {label}", callback_data=f"trans:{code}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def _priority_badge(is_pro: bool) -> str:
    return "⚡ Высокий (PRO)" if is_pro else "Обычный"



# ---------- Быстрый предчек размера TG-файлов ----------

def _get_tg_file_size_mb(update: Update, file_type: str) -> float | None:
    msg = update.message
    try:
        if file_type == "voice" and msg.voice:
            return (msg.voice.file_size or 0) / (1024 * 1024)
        if file_type == "audio" and msg.audio:
            return (msg.audio.file_size or 0) / (1024 * 1024)
        if file_type == "video" and msg.video:
            return (msg.video.file_size or 0) / (1024 * 1024)
        if file_type == "video_note" and msg.video_note:
            return (msg.video_note.file_size or 0) / (1024 * 1024)
        if file_type == "document" and msg.document:
            return (msg.document.file_size or 0) / (1024 * 1024)
    except Exception:
        pass
    return None

async def _reject_if_too_big(update: Update, file_type: str) -> bool:
    """
    Если TG-файл больше MAX_FILE_SIZE_MB — сразу просим прислать ссылку (до URL_MAX_FILE_SIZE_MB).
    Возвращает True, если нужно прервать дальнейшую обработку.
    """
    size_mb = _get_tg_file_size_mb(update, file_type)
    if size_mb is None:
        return False
    if size_mb > float(MAX_FILE_SIZE_MB):
        await update.message.reply_text(
            f"❌ Файл больше {MAX_FILE_SIZE_MB} МБ и через Telegram не обрабатывается.\n\n"
            f"👉 Пришлите ссылку (YouTube / Я.Диск / Google Drive) — по ссылке принимаем файлы до {URL_MAX_FILE_SIZE_MB} МБ.",
            reply_markup=_main_menu_keyboard()
        )
        return True
    return False

# ---------- Команды ----------

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_pro = storage.is_pro(user_id)

    # Базовый текст лимитов из менеджера
    base_text = limit_manager.get_usage_info(user_id)

    # Статус очереди
    q = task_queue.get_queue_stats()
    queue_line = (
        f"Текущая очередь: {q['queue_size']} | "
        f"Активных: {q['active_tasks']}/{q['max_concurrent']}"
    )

    # Приоритет обслуживания
    prio_line = f"Приоритет обслуживания: {_priority_badge(is_pro)}"

    text = f"{base_text}\n\n{prio_line}\n{queue_line}"

    # Кнопки:
    #  - для всех: докупить минуты (как было)
    #  - для НЕ PRO (если настроен провайдер): кнопка «⚡ Ускорить с PRO»
    rows = []

    # PRO апгрейд (если доступен провайдер)
    if not is_pro and payment_manager:
        try:
            payment_url = payment_manager.get_payment_url(user_id)
            rows.append([InlineKeyboardButton("⚡ Ускорить с PRO", url=payment_url)])
        except Exception:
            # молча пропускаем, если провайдер не готов
            pass

    # Докупить минуты на сегодня
    options = [10, 30, 60]
    for m in options:
        amount = m * float(OVERAGE_PRICE_RUB)
        rows.append([
            InlineKeyboardButton(
                f"Докупить {m} мин — {amount:.0f} ₽",
                callback_data=f"buy:{m}:{int(amount)}"
            )
        ])

    kb = InlineKeyboardMarkup(rows) if rows else None

    await update.message.reply_text(
        text + ("\n\nНужно больше минут сегодня? Докупите пакет:" if rows else ""),
        reply_markup=kb
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Как использовать AI-Vera:*\n\n"
        f"• Отправьте голосовое/аудио/видео (до {MAX_FILE_SIZE_MB} МБ) — MP3, WAV, OGG, M4A, MP4, AVI и др.\n"
        f"• Или пришлите ссылку: YouTube / Яндекс.Диск / Google Drive (до {URL_MAX_FILE_SIZE_MB} МБ)\n\n"
        "*Подсказка:* длинные тексты бот сам отправит файлом .txt.\n"
        "Используйте /stats для проверки лимитов и докупки минут.",
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(),
    )

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if storage.is_pro(user_id):
        await update.message.reply_text(
            "🎉 У вас уже есть PRO:\n• Больше минут в день\n• Приоритетная обработка\n• Все форматы",
            reply_markup=_main_menu_keyboard(),
        )
        return
    if not payment_manager:
        await update.message.reply_text("❌ Платежи временно недоступны.", reply_markup=_main_menu_keyboard())
        return
    payment_url = payment_manager.get_payment_url(user_id)
    await update.message.reply_text(
            "💎 *Перейдите на PRO версию!*\n\n"
            "Преимущества:\n"
            "• 🕐 больше минут в день\n"
            "• ⚡ приоритет обработки\n"
            "• 📁 все форматы\n\n"
            f"[Оплатить PRO]({payment_url})",
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=_main_menu_keyboard(),
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Только для администраторов.")
        return
    stats = task_queue.get_queue_stats()
    pro_users_count = storage.count_pro()
    await update.message.reply_text(
        "👑 *Админ-панель*\n\n"
        f"PRO пользователей: {pro_users_count}\n"
        f"Задач в очереди: {stats['queue_size']}\n"
        f"Активных задач: {stats['active_tasks']}\n",
        parse_mode="Markdown",
    )

async def add_pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Только для админов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /addpro <user_id>")
        return
    try:
        target = int(context.args[0])
        storage.add_pro(target)
        await update.message.reply_text(f"✅ Пользователь {target} добавлен в PRO")
    except ValueError:
        await update.message.reply_text("Неверный формат user_id")

async def remove_pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Только для админов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /removepro <user_id>")
        return
    try:
        target = int(context.args[0])
        storage.remove_pro(target)
        await update.message.reply_text(f"✅ Пользователь {target} удалён из PRO")
    except ValueError:
        await update.message.reply_text("Неверный формат user_id")

async def queue_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Только для админов.")
        return
    stats = task_queue.get_queue_stats()
    await update.message.reply_text(
        "📊 Очередь:\n"
        f"• В очереди: {stats['queue_size']}\n"
        f"• Активных: {stats['active_tasks']}\n"
        f"• Всего: {stats['total_tasks']}\n"
        f"• Параллельно: {stats['max_concurrent']}\n"
    )

async def backend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Только для администраторов.")
        return
    await update.message.reply_text(
        "⚙️ Текущие настройки распознавания:\n"
        f"• Бэкенд: {WHISPER_BACKEND}\n"
        f"• Модель: {WHISPER_MODEL}"
    )

# ---------- Обработка через очередь ----------

async def process_via_queue(update: Update, context: ContextTypes.DEFAULT_TYPE, file_type: str, url: str | None = None):
    queue_msg = await update.message.reply_text("📋 Задача поставлена в очередь...")
    try:
        user_id = update.effective_user.id
        priority = 0 if storage.is_pro(user_id) else 1
        task_id = await task_queue.add_task(
            task_manager.process_transcription_task,
            update, context, file_type, url,
            priority=priority
        )

        while True:
            await asyncio.sleep(2)
            status = task_queue.get_task_status(task_id)
            s = status.get("status")
            if s == "completed":
                result = status.get("result", {})
                if result.get("success"):
                    # Кэш последнего результата: для кнопок экспорта
                    context.user_data["last_transcription"] = {
                        "text": result.get("text", ""),
                        "segments": result.get("segments") or [],
                        "title": result.get("title") or "Транскрибация",
                        "pdf_path": result.get("pdf_path"),
                        "detected_language": result.get("detected_language"),
                    }


                    head_lines = []
                    if result.get("title"):
                        head_lines.append(f"✅ *{result['title']}*")

                    dur = result.get("duration") or 0
                    head_lines.append(f"Длительность: {format_seconds(int(dur))}")
                    
                    # Бейдж приоритета обслуживания
                    is_pro_now = storage.is_pro(update.effective_user.id)
                    head_lines.append(f"Приоритет: {_priority_badge(is_pro_now)}")

                    if result.get("detected_language"):
                        head_lines.append(f"Язык: {_lang_pretty(result['detected_language'])}")

                    # Кол-во слов
                    if isinstance(result.get("word_count"), int) and result["word_count"] > 0:
                        head_lines.append(f"Слов: {result['word_count']}")

                    # Время обработки
                    if result.get("processing_time_s") is not None:
                        # округлим/приведём к человекочитаемому формату
                        secs = result["processing_time_s"]
                        head_lines.append(f"Обработка: {secs:.1f} c")

                    head = "\n".join(head_lines) + "\n\n"

                    text = result.get("text", "") or ""
                    MESSAGE_LIMIT = 3900  # запас к 4096
                    if len(text) > MESSAGE_LIMIT:
                        # 1) короткий анонс
                        if head:
                            await update.message.reply_text(head, parse_mode="Markdown")
                        await update.message.reply_text("📝 Текст длинный — отправляю файлом .txt")

                        # 2) полный текст .txt
                        downloads = _ensure_downloads_dir()
                        filename_base = f"transcription_{uuid.uuid4().hex[:8]}"
                        txt_path = os.path.join(downloads, f"{filename_base}.txt")
                        with open(txt_path, "w", encoding="utf-8") as f:
                            f.write(text)
                        with open(txt_path, "rb") as f:
                            await update.message.reply_document(
                                InputFile(f, filename=os.path.basename(txt_path)),
                                caption="📝 Полный текст",
                            )
                        os.remove(txt_path)

                        # 3) если есть авто-PDF — отправим
                        if result.get("pdf_path"):
                            try:
                                with open(result["pdf_path"], "rb") as f:
                                    await update.message.reply_document(
                                        InputFile(f, filename="transcription.pdf"),
                                        caption="📄 PDF версия",
                                    )
                            except Exception as e:
                                logger.error(f"Ошибка отправки PDF: {e}")
                    else:
                        # короткий текст — прямо в сообщении
                        await update.message.reply_text(head + f"📝 Результат:\n\n{text}", parse_mode="Markdown")

                    # Инлайн-кнопки экспорта
                    keyboard = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton("📄 PDF", callback_data="export:pdf"),
                                InlineKeyboardButton("📝 TXT", callback_data="export:txt"),
                            ],
                            [InlineKeyboardButton("⏱️ SRT", callback_data="export:srt")],
                        ]
                    )
                    await update.message.reply_text("Экспортировать в файл:", reply_markup=keyboard)
                    await update.message.reply_text("Нужен перевод текста?", reply_markup=_translation_keyboard())
                    await update.message.reply_text(
                        "📊 Хотите посмотреть аналитику текста?",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("📊 Показать аналитику", callback_data="analytics")]]
                        )
                    )


                    await queue_msg.edit_text("✅ Готово!")
                else:
                    err = result.get("error")
                    if err == "limit_exceeded":
                        # Предложить фиксированные пакеты докупки минут
                        options = [10, 30, 60]
                        rows = []
                        for m in options:
                            amount = m * float(OVERAGE_PRICE_RUB)
                            rows.append([
                                InlineKeyboardButton(
                                    f"Докупить {m} мин — {amount:.0f} ₽",
                                    callback_data=f"buy:{m}:{int(amount)}"
                                )
                            ])
                        kb = InlineKeyboardMarkup(rows)
                        await queue_msg.edit_text(result.get("message", "Превышен лимит."))
                        await update.message.reply_text("Можно докупить минуты на сегодня:", reply_markup=kb)
                    elif err == "download_failed":
                        await queue_msg.edit_text("❌ Не удалось скачать файл/ссылку.")
                    else:
                        await queue_msg.edit_text("❌ Ошибка при обработке.")
                break

            elif s == "failed":
                await queue_msg.edit_text("❌ Ошибка при выполнении задачи.")
                break

            elif s == "processing":
                stats = task_queue.get_queue_stats()
                pos = stats["queue_size"] + stats["active_tasks"]
                await queue_msg.edit_text(
                    f"⏳ Обрабатываю... Позиция: {pos}\n"
                    f"Активных задач: {stats['active_tasks']}/{stats['max_concurrent']}"
                )
    except Exception as e:
        logger.error(f"Ошибка очереди: {e}")
        await queue_msg.edit_text("❌ Системная ошибка.")

# ---------- Экспорт по кнопкам ----------

def _ensure_downloads_dir() -> str:
    d = "downloads"
    os.makedirs(d, exist_ok=True)
    return d

def _srt_time(t: float) -> str:
    ms = int(round((t - int(t)) * 1000))
    s = int(t) % 60
    m = (int(t) // 60) % 60
    h = int(t) // 3600
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def _make_srt_content(segments: list[dict]) -> str:
    lines = []
    for idx, seg in enumerate(segments, 1):
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        text = (seg.get("text") or "").strip()
        lines.append(str(idx))
        lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"

async def export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kind = (query.data or "").split(":", 1)[-1]
    data = context.user_data.get("last_transcription")
    if not data:
        await query.edit_message_text("Нет недавнего результата для экспорта.")
        return

    title = data.get("title") or "transcription"
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip() or "transcription"
    downloads = _ensure_downloads_dir()
    filename_base = f"{safe_title}_{uuid.uuid4().hex[:8]}"

    try:
        if kind == "pdf":
            pdf_path = data.get("pdf_path")
            if not pdf_path:
                pdf_path = os.path.join(downloads, f"{filename_base}.pdf")
                pdf_generator.generate_transcription_pdf(data["text"], pdf_path, title=title)
            with open(pdf_path, "rb") as f:
                await query.message.reply_document(
                    InputFile(f, filename=os.path.basename(pdf_path)),
                    caption="📄 PDF файл",
                )
            if not data.get("pdf_path") and os.path.exists(pdf_path):
                os.remove(pdf_path)

        elif kind == "txt":
            txt_path = os.path.join(downloads, f"{filename_base}.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(data["text"])
            with open(txt_path, "rb") as f:
                await query.message.reply_document(
                    InputFile(f, filename=os.path.basename(txt_path)),
                    caption="📝 TXT файл",
                )
            os.remove(txt_path)

        elif kind == "srt":
            segments = data.get("segments") or []
            if not segments:
                await query.edit_message_text("⏱️ Нет сегментов для SRT.")
                return
            srt_path = os.path.join(downloads, f"{filename_base}.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(_make_srt_content(segments))
            with open(srt_path, "rb") as f:
                await query.message.reply_document(
                    InputFile(f, filename=os.path.basename(srt_path)),
                    caption="⏱️ SRT файл",
                )
            os.remove(srt_path)

        else:
            await query.edit_message_text("Неизвестный формат экспорта.")
    except Exception:
        logger.exception("Export error")
        await query.edit_message_text("❌ Ошибка экспорта файла.")


async def export_translation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kind = (query.data or "").split(":", 1)[-1]

    data = context.user_data.get("last_translation")
    if not data or not data.get("text"):
        await query.edit_message_text("Нет сохранённого перевода для экспорта.")
        return

    title = f"{data.get('title') or 'Транскрибация'} — перевод ({data.get('lang','?')})"
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip() or "translation"
    downloads = _ensure_downloads_dir()
    filename_base = f"{safe_title}_{uuid.uuid4().hex[:8]}"

    try:
        if kind == "pdf":
            pdf_path = os.path.join(downloads, f"{filename_base}.pdf")
            pdf_generator.generate_transcription_pdf(data["text"], pdf_path, title=title)
            with open(pdf_path, "rb") as f:
                await query.message.reply_document(InputFile(f, filename=os.path.basename(pdf_path)),
                                                   caption="📄 PDF перевод")
            os.remove(pdf_path)

        elif kind == "txt":
            txt_path = os.path.join(downloads, f"{filename_base}.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(data["text"])
            with open(txt_path, "rb") as f:
                await query.message.reply_document(InputFile(f, filename=os.path.basename(txt_path)),
                                                   caption="📝 TXT перевод")
            os.remove(txt_path)
        else:
            await query.edit_message_text("Неизвестный формат экспорта перевода.")
    except Exception:
        logger.exception("Export translation error")
        await query.edit_message_text("❌ Ошибка экспорта перевода.")


    async def translate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data.get("last_transcription")
    if not data or not data.get("text"):
        await query.edit_message_text("Нет текста для перевода.")
        return

    # формат: trans:<lang>
    try:
        target_lang = (query.data or "").split(":", 1)[1].strip().lower()
    except Exception:
        await query.edit_message_text("Не указан язык перевода.")
        return

    text = data["text"]
    title = data.get("title") or "Транскрибация"

    try:
        await query.edit_message_text("🌐 Выполняю перевод, подождите...")
        translated = await asyncio.to_thread(translate_text, text, target_lang, "auto")

        # Сохраняем перевод для последующего экспорта
        context.user_data["last_translation"] = {
            "text": translated,
            "lang": target_lang,
            "title": title,
        }

        MESSAGE_LIMIT = 3900
        lang_str = _lang_pretty(target_lang)
        head = f"🌐 *Перевод* → {lang_str}\nИз: *{title}*\n\n"

        if len(translated) <= MESSAGE_LIMIT:
            await query.message.reply_text(head + translated, parse_mode="Markdown")
        else:
            downloads = _ensure_downloads_dir()
            safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip() or "transcription"
            filename = f"translation_{safe_title}_{target_lang}_{uuid.uuid4().hex[:6]}.txt"
            path = os.path.join(downloads, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(translated)
            with open(path, "rb") as f:
                await query.message.reply_document(
                    InputFile(f, filename=filename),
                    caption=f"🌐 Перевод → {lang_str}"
                )
            os.remove(path)

        # Клавиатура экспорта перевода
        kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("📄 PDF перевода", callback_data="t_export:pdf"),
                InlineKeyboardButton("📝 TXT перевода", callback_data="t_export:txt"),
            ]]
        )
        await query.message.reply_text("Экспортировать перевод:", reply_markup=kb)

    except Exception:
        logger.exception("Translate callback error")
        await query.edit_message_text("❌ Ошибка перевода. Попробуйте позже.")

async def analytics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data.get("last_transcription")
    if not data or not data.get("text"):
        await query.edit_message_text("Нет текста для аналитики.")
        return

    text = data["text"]
    # берём язык, если сохранили в last_transcription, иначе простая эвристика
    lang_code = data.get("detected_language")
    if not lang_code:
        try:
            lang_code = "ru" if any("а" <= ch <= "я" or "А" <= ch <= "Я" for ch in text) else "en"
        except Exception:
            lang_code = "en"

    metrics = analyze_text(text, lang_code)
    report = build_report_md(metrics)
    await query.message.reply_text(report, parse_mode="Markdown")


# ---------- Покупка докупки минут ----------

async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # формат: buy:<minutes>:<amount_int>
    parts = (query.data or "").split(":")
    try:
        minutes = int(parts[1])
        amount_int = int(parts[2])
    except Exception:
        await query.edit_message_text("Неверный параметр покупки.")
        return

    user_id = query.from_user.id
    amount = float(amount_int)

    if not payment_manager:
        await query.edit_message_text("❌ Платежи недоступны.")
        return

    try:
        if hasattr(payment_manager, "get_topup_url"):
            topup_url = payment_manager.get_topup_url(user_id=user_id, minutes=minutes, amount=amount)
            await query.edit_message_text(
                f"Для докупки {minutes} мин перейдите по ссылке:\n{topup_url}"
            )
        else:
            await query.edit_message_text("❌ Провайдер оплаты не поддерживает докупку минут.")
    except Exception:
        logger.exception("buy_callback error")
        await query.edit_message_text("❌ Ошибка при подготовке оплаты.")

# ---------- Хэндлеры сообщений (с предчеком размера) ----------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_too_big(update, "voice"):
        return
    await process_via_queue(update, context, "voice")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_too_big(update, "audio"):
        return
    await process_via_queue(update, context, "audio")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_too_big(update, "video"):
        return
    await process_via_queue(update, context, "video")

async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_too_big(update, "video_note"):
        return
    await process_via_queue(update, context, "video_note")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    name = (doc.file_name or "").lower()
    if any(
        name.endswith(ext)
        for ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv")
    ):
        if await _reject_if_too_big(update, "document"):
            return
        await process_via_queue(update, context, "document")
    else:
        await update.message.reply_text("❌ Пожалуйста, отправьте аудио или видео файл.", reply_markup=_main_menu_keyboard())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # Кнопки из меню
    if text == "⏱ Статус":
        return await stats_command(update, context)
    if text == "ℹ️ Помощь":
        return await help_command(update, context)
    if text == "💎 PRO":
        return await premium_command(update, context)
    if text == "🔗 Отправить ссылку":
        return await update.message.reply_text("Пришлите ссылку на YouTube/Я.Диск/Google Drive одним сообщением.")

    # Ссылка
    if text.startswith(("http://", "https://", "www.")):
        return await process_via_queue(update, context, "url", text)

    await update.message.reply_text(
        "Отправьте ссылку (YouTube/Я.Диск/GDrive) или медиафайл.",
        reply_markup=_main_menu_keyboard()
    )

    
# ---------- Точка входа с «мягкой защитой» ----------

def main():
    # Миграция PRO из ENV → Redis/Postgres
    run_startup_migrations()

    async def _post_init(_):
        await task_queue.start()

    async def _post_shutdown(_):
        await task_queue.stop()

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("queue", queue_stats_command))
    app.add_handler(CommandHandler("premium", premium_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("addpro", add_pro_command))
    app.add_handler(CommandHandler("removepro", remove_pro_command))
    app.add_handler(CommandHandler("backend", backend_command))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(export_callback, pattern=r"^export:"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(translate_callback, pattern=r"^trans:"))
    app.add_handler(CallbackQueryHandler(export_translation_callback, pattern=r"^t_export:"))
    app.add_handler(CallbackQueryHandler(analytics_callback, pattern=r"^analytics$"))



    logger.info("Запуск бота AI-Vera (polling)...")

    try:
        # реже опрашиваем и очищаем отложенные апдейты на старте
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            poll_interval=3.0,
            drop_pending_updates=True,
        )
    except Conflict:
        # Мягкая защита: другой процесс уже делает getUpdates этим токеном
        logger.error(
            "❌ Conflict: другой процесс бота уже делает getUpdates этим токеном. "
            "Остановите дубликат (локальный скрипт, второй инстанс на хостинге, включённый вебхук)."
        )
        try:
            asyncio.run(task_queue.stop())
        except Exception:
            pass
        sys.exit(0)
    except Exception:
        logger.exception("Критическая ошибка приложения.")
        try:
            asyncio.run(task_queue.stop())
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()

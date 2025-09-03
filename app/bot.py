# app/bot.py
import logging
import asyncio
import os
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

from app.config import (
    TELEGRAM_BOT_TOKEN,
    WHISPER_BACKEND,
    WHISPER_MODEL,
    ADMIN_USER_IDS,
    OVERAGE_PRICE_RUB,
)
from app import storage
from app.utils import format_seconds
from app.task_queue import task_queue
from app.task_manager import task_manager
from app.bootstrap import run_startup_migrations
from app.payments_bootstrap import payment_manager
from app.pdf_generator import pdf_generator

# Приглушим шум от httpx (getUpdates каждые N секунд)
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- На случай, если инстанс не экспортирован (подстраховка от ImportError) ---
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


# ---------- Команды ----------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я — AI-Vera. За пару шагов превращу аудио или видео в текст:\n\n"
        "1) Отправь голосовое, аудио или видео (до 20 МБ)\n"
        "   — поддерживаются MP3/WAV/OGG/M4A/MP4 и др.\n"
        "2) Или пришли ссылку на YouTube, Яндекс.Диск или Google Drive\n\n"
        "Полезное:\n"
        "• ⏱ /stats — твои лимиты и докупка минут\n"
        "• ℹ️ /help — форматы и подсказки\n"
        "• 💎 /premium — перейти на PRO\n\n"
        "Готов? Выбери действие в меню ниже или просто пришли файл/ссылку."
    )
    await update.message.reply_text(text, reply_markup=_main_menu_keyboard())


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = limit_manager.get_usage_info(user_id)

    # Кнопки докупки минут на сегодня (фиксированные пакеты)
    options = [10, 30, 60]
    rows = []
    for m in options:
        amount = m * OVERAGE_PRICE_RUB
        rows.append([
            InlineKeyboardButton(
                f"Докупить {m} мин — {amount:.0f} ₽",
                callback_data=f"buy:{m}:{int(amount)}"
            )
        ])
    kb = InlineKeyboardMarkup(rows)

    await update.message.reply_text(
        text + "\n\nНужно больше минут сегодня? Докупите пакет:",
        reply_markup=kb
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Как использовать AI-Vera:*\n\n"
        "• Отправьте голосовое/аудио/видео (MP3, WAV, OGG, M4A, MP4, AVI и др.)\n"
        "• Или пришлите ссылку: YouTube / Яндекс.Диск / Google Drive\n\n"
        "*Важно:* размер файла ≤ 20 МБ.\n"
        "Используйте /stats для проверки лимитов и докупки минут.",
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(),
    )


async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if storage.is_pro(user_id):
        await update.message.reply_text(
            "🎉 У вас уже есть PRO:\n• 120 мин/день (по умолчанию)\n• Приоритетная обработка\n• Все форматы",
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
        task_id = await task_queue.add_task(task_manager.process_transcription_task, update, context, file_type, url)
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
                    }

                    head = ""
                    if result.get("title"):
                        head = f"✅ *{result['title']}*\nДлительность: {format_seconds(result['duration'])}\n\n"
                    text = result.get("text", "")
                    if len(text) > 4000:
                        if head:
                            await update.message.reply_text(head, parse_mode="Markdown")
                        for i in range(0, len(text), 4000):
                            await update.message.reply_text(text[i:i+4000])
                    else:
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

                    # Если авто-PDF был создан — отправим его
                    if result.get("pdf_path"):
                        try:
                            with open(result["pdf_path"], "rb") as f:
                                await update.message.reply_document(
                                    InputFile(f, filename="transcription.pdf"),
                                    caption="📄 PDF версия транскрипции",
                                )
                        except Exception as e:
                            logger.error(f"Ошибка отправки PDF: {e}")

                    await queue_msg.edit_text("✅ Готово!")
                else:
                    err = result.get("error")
                    if err == "limit_exceeded":
                        # Предложить фиксированные пакеты докупки минут
                        options = [10, 30, 60]
                        rows = []
                        for m in options:
                            amount = m * OVERAGE_PRICE_RUB
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


# ---------- Хэндлеры сообщений ----------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_via_queue(update, context, "voice")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_via_queue(update, context, "audio")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_via_queue(update, context, "video")


async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_via_queue(update, context, "video_note")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    name = (doc.file_name or "").lower()
    if any(
        name.endswith(ext)
        for ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv")
    ):
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
        return await update.message.reply_text("Пришли ссылку на YouTube/Я.Диск/Google Drive одним сообщением.")

    # Ссылка
    if text.startswith(("http://", "https://", "www.")):
        return await process_via_queue(update, context, "url", text)

    await update.message.reply_text(
        "Отправьте ссылку (YouTube/Я.Диск/GDrive) или медиафайл.",
        reply_markup=_main_menu_keyboard()
    )


# ---------- Точка входа ----------

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

    logger.info("Запуск бота AI-Vera (polling)...")
    # Чуть реже опрашиваем, и очищаем отложенные апдейты на старте
    app.run_polling(allowed_updates=Update.ALL_TYPES, poll_interval=3.0, drop_pending_updates=True)


if __name__ == "__main__":
    main()

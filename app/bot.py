import logging
import asyncio
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextType
from app.config import TELEGRAM_BOT_TOKEN, ADMIN_USER_IDS, payment_manager, PRO_USER_IDS
from app.limit_manager import limit_manager
from app.task_queue import task_queue
from app.task_manager import task_manager
from app.utils import format_seconds

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Команды ----------

async def start_command(update: Update, context: ContextType.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я — AI-Vera, бот для транскрибации аудио и видео в текст.\n"
        "Отправь голосовое, аудио или видео (до 20 МБ) — я всё распознаю.\n\n"
        "Команды:\n"
        "/start — это сообщение\n"
        "/stats — твои лимиты\n"
        "/help — помощь\n"
        "/queue — статус очереди (админ)\n"
        "/premium — PRO-статус\n"
        "/admin — админ-панель"
    )

async def stats_command(update: Update, context: ContextType.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(limit_manager.get_usage_info(user_id))

async def help_command(update: Update, context: ContextType.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Как использовать AI-Vera:*\n\n"
        "1) Голосовые — пришлите голос.\n"
        "2) Аудио/видео — отправьте файл (MP3, WAV, MP4, AVI и др.).\n"
        "3) Ссылки (YouTube, Я.Диск, GDrive) — пришлите URL текстом.\n\n"
        "*Важно:* размер файла ≤ 20 МБ.\n"
        "Используйте /stats для проверки лимитов.",
        parse_mode='Markdown'
    )

async def premium_command(update: Update, context: ContextType.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in PRO_USER_IDS:
        await update.message.reply_text(
            "🎉 У вас уже есть PRO:\n• 120 мин/день\n• Приоритетная обработка\n• Все форматы"
        )
        return
    if not payment_manager:
        await update.message.reply_text("❌ Платежи временно недоступны.")
        return
    payment_url = payment_manager.get_payment_url(user_id)
    await update.message.reply_text(
        "💎 *Перейдите на PRO версию!*\n\n"
        "Преимущества:\n"
        "• 🕐 120 минут/день\n"
        "• ⚡ Приоритет\n"
        "• 📁 Все форматы\n\n"
        f"[Оплатить PRO]({payment_url})",
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

async def admin_command(update: Update, context: ContextType.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Только для администраторов.")
        return
    stats = task_queue.get_queue_stats()
    await update.message.reply_text(
        "👑 *Админ-панель*\n\n"
        f"PRO пользователей: {len(PRO_USER_IDS)}\n"
        f"Задач в очереди: {stats['queue_size']}\n"
        f"Активных задач: {stats['active_tasks']}\n",
        parse_mode='Markdown'
    )

async def add_pro_command(update: Update, context: ContextType.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Только для админов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /addpro <user_id>")
        return
    try:
        target = int(context.args[0])
        if target not in PRO_USER_IDS:
            PRO_USER_IDS.append(target)
        await update.message.reply_text(f"✅ Пользователь {target} добавлен в PRO")
    except ValueError:
        await update.message.reply_text("Неверный формат user_id")

async def remove_pro_command(update: Update, context: ContextType.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Только для админов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /removepro <user_id>")
        return
    try:
        target = int(context.args[0])
        if target in PRO_USER_IDS:
            PRO_USER_IDS.remove(target)
        await update.message.reply_text(f"✅ Пользователь {target} удалён из PRO")
    except ValueError:
        await update.message.reply_text("Неверный формат user_id")

async def queue_stats_command(update: Update, context: ContextType.DEFAULT_TYPE):
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

# ---------- Обработка через очередь ----------

async def process_via_queue(update: Update, context: ContextType.DEFAULT_TYPE, file_type: str, url: str | None = None):
    queue_msg = await update.message.reply_text("📋 Задача поставлена в очередь...")
    try:
        task_id = await task_queue.add_task(task_manager.process_transcription_task, update, context, file_type, url)
        while True:
            await asyncio.sleep(2)
            status = task_queue.get_task_status(task_id)
            s = status.get('status')
            if s == 'completed':
                result = status.get('result', {})
                if result.get('success'):
                    head = ""
                    if result.get('title'):
                        head = f"✅ *{result['title']}*\nДлительность: {format_seconds(result['duration'])}\n\n"
                    text = result.get('text', '')
                    if len(text) > 4000:
                        if head:
                            await update.message.reply_text(head, parse_mode='Markdown')
                        for i in range(0, len(text), 4000):
                            await update.message.reply_text(text[i:i+4000])
                    else:
                        await update.message.reply_text(head + f"📝 Результат:\n\n{text}", parse_mode='Markdown')

                    if result.get('pdf_path'):
                        try:
                            with open(result['pdf_path'], 'rb') as f:
                                await update.message.reply_document(InputFile(f, filename="transcription.pdf"),
                                                                   caption="📄 PDF версия транскрипции")
                        except Exception as e:
                            logger.error(f"Ошибка отправки PDF: {e}")

                    await queue_msg.edit_text("✅ Готово!")
                else:
                    err = result.get('error')
                    if err == 'limit_exceeded':
                        await queue_msg.edit_text(result.get('message', 'Превышен лимит'))
                    elif err == 'download_failed':
                        await queue_msg.edit_text("❌ Не удалось скачать файл/ссылку.")
                    else:
                        await queue_msg.edit_text("❌ Ошибка при обработке.")
                break

            elif s == 'failed':
                await queue_msg.edit_text("❌ Ошибка при выполнении задачи.")
                break

            elif s == 'processing':
                stats = task_queue.get_queue_stats()
                pos = stats['queue_size'] + stats['active_tasks']
                await queue_msg.edit_text(
                    f"⏳ Обрабатываю... Позиция: {pos}\n"
                    f"Активных задач: {stats['active_tasks']}/{stats['max_concurrent']}"
                )
    except Exception as e:
        logger.error(f"Ошибка очереди: {e}")
        await queue_msg.edit_text("❌ Системная ошибка.")

# ---------- Хэндлеры ----------

async def handle_voice(update: Update, context: ContextType.DEFAULT_TYPE):
    await process_via_queue(update, context, 'voice')

async def handle_audio(update: Update, context: ContextType.DEFAULT_TYPE):
    await process_via_queue(update, context, 'audio')

async def handle_video(update: Update, context: ContextType.DEFAULT_TYPE):
    await process_via_queue(update, context, 'video')

async def handle_video_note(update: Update, context: ContextType.DEFAULT_TYPE):
    await process_via_queue(update, context, 'video_note')

async def handle_document(update: Update, context: ContextType.DEFAULT_TYPE):
    doc = update.message.document
    name = (doc.file_name or "").lower()
    if any(name.endswith(ext) for ext in ('.mp3', '.wav', '.ogg', '.m4a', '.mp4', '.avi', '.mov')):
        await process_via_queue(update, context, 'document')
    else:
        await update.message.reply_text("❌ Пожалуйста, отправьте аудио или видео файл.")

async def handle_text(update: Update, context: ContextType.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text.startswith(('http://', 'https://', 'www.')):
        await process_via_queue(update, context, 'url', text)
    else:
        await update.message.reply_text("Отправьте ссылку (YouTube/Я.Диск/GDrive) или медиафайл.")

# ---------- Точка входа ----------

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("queue", queue_stats_command))
    app.add_handler(CommandHandler("premium", premium_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("addpro", add_pro_command))
    app.add_handler(CommandHandler("removepro", remove_pro_command))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def _post_init(_):
        await task_queue.start()
    async def _post_stop(_):
        await task_queue.stop()

    logger.info("Запуск бота AI-Vera (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, post_init=_post_init, post_stop=_post_stop)

if __name__ == "__main__":
    main()

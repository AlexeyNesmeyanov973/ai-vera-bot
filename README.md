# AI-Vera Transcribator 🤖

Telegram-бот для транскрибации аудио и видео в текст с поддержкой AI.

## Возможности

- 🎤 Транскрибация голосовых сообщений
- 📁 Обработка аудио и видео файлов (до **20 МБ**)
- 🌐 Поддержка YouTube, Яндекс.Диск, Google Drive
- 💎 PRO-статус с увеличенными лимитами (оплата через **Prodamus**)
- 📄 Генерация PDF с результатами (поддержка кириллицы)
- ⚡ Асинхронная обработка с очередями

---

## Установка на Render.com

1. **Форкните репозиторий** и подключите к Render.com.
2. **Добавьте переменные окружения** в панели Render (для обоих сервисов — Worker и Web):
   - `TELEGRAM_BOT_TOKEN` — токен от @BotFather
   - `ADMIN_USER_IDS` — ID администраторов через запятую
   - `PRODAMUS_WEBHOOK_SECRET` — секрет вебхука Prodamus
   - `PRODAMUS_PAYMENT_LINK` — платёжная страница Prodamus (из кабинета)
   - (опц.) `PRODAMUS_PRO_AMOUNT` — сумма PRO, по умолчанию `299.0`
   - (опц.) `WHISPER_BACKEND` — `faster` (по умолчанию) или `openai`
   - (опц.) `WHISPER_MODEL` — например `small`
   - (опц.) `REDIS_URL`, `DATABASE_URL`
3. **Настройте webhook** в Prodamus на:
https://your-app-name.onrender.com/webhook/prodamus

yaml
Копировать код
4. **Деплой** — Render автоматически соберёт и запустит приложение.

---

## Локальная разработка

1. Установите зависимости:
```bash
pip install -r requirements.txt
Запустите бота:

bash
Копировать код
python -m app.bot
Запустите веб-сервис (метрики/вебхук):

bash
Копировать код
python -m app.web

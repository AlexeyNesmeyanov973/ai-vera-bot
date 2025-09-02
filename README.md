# AI-Vera Transcribator 🤖

Telegram-бот для транскрибации аудио и видео в текст с поддержкой AI.

## Возможности

- 🎤 Транскрибация голосовых сообщений
- 📁 Обработка аудио и видео файлов (до 20 МБ)
- 🌐 Поддержка YouTube, Яндекс.Диск, Google Drive
- 💎 PRO-статус с увеличенными лимитами
- 📄 Генерация PDF с результатами
- ⚡ Асинхронная обработка с очередями

## Установка на Render.com

1. **Форкните репозиторий** и подключите к Render.com
2. **Добавьте переменные окружения** в панели Render:
   - `TELEGRAM_BOT_TOKEN` - токен от @BotFather
   - `ADMIN_USER_IDS` - ID администраторов через запятую
   - `PAYDUS_WEBHOOK_SECRET` - секрет из кабинета Paydmus
3. **Настройте webhook** в Paydmus на: `https://your-app-name.onrender.com/webhook/paydmus`
4. **Деплой** - Render автоматически соберет и запустит приложение

## Локальная разработка

1. Установите зависимости:
```bash
pip install -r requirements.txt

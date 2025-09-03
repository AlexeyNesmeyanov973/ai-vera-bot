# AI-Vera Transcribator 🤖

Телеграм-бот для транскрибации аудио/видео в текст (Whisper: faster/openai), экспорт PDF/TXT/SRT, PRO через YooKassa/Prodamus.

## Развёртывание на Render

1. Репозиторий → Render → New + Docker.
2. Добавьте два сервиса:
   - **Worker**: `dockerCommand: python -m app.bot`
   - **Web**: `dockerCommand: exec gunicorn -w 2 -b 0.0.0.0:${PORT:-8000} app.web:app`
3. Установите переменные окружения (см. `.env.example`).

## Переменные окружения

Смотрите `.env.example`. Минимум для запуска:
- `TELEGRAM_BOT_TOKEN`
- (опционально) `YOOKASSA_*` **или** `PRODAMUS_*`
- (опционально) `REDIS_URL`, `DATABASE_URL`

## Команды бота

- `/start`, `/help`, `/stats`
- `/premium` — ссылка на оплату
- `/admin`, `/queue`, `/backend` — для админов

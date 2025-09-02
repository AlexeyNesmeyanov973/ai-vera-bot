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
### Служебные команды
- `/backend` (админ): показать текущий бэкенд распознавания и модель.

### Prometheus метрики
- Эндпойнт: `GET /metrics` (на веб-сервисе).
- Метрики:
  - `web_requests_total{endpoint,method}`
  - `webhook_errors_total{reason}`
  - `webhook_latency_seconds` (гистограмма времени обработки).

### Миграция PRO
- При старте `worker` и `web` выполняется миграция `PRO_USER_IDS` (из env) в Redis/Postgres.
- Идемпотентна. После успешной миграции можно очистить `PRO_USER_IDS` в окружении.

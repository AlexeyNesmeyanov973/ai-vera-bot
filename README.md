# AI-Vera Transcribator 🤖

Telegram-бот для транскрибации аудио и видео в текст с использованием AI. Поддерживает очереди, генерацию PDF и платную подписку через Prodamus.

---

## 🚀 Возможности

- 🎤 Распознавание голосовых сообщений
- 📁 Обработка аудио и видео файлов (до 20 МБ)
- 🌐 Поддержка ссылок (YouTube, Яндекс.Диск, Google Drive)
- 💎 PRO-статус с увеличенными лимитами (через оплату)
- 📄 Генерация PDF с результатами
- ⚡ Асинхронная очередь задач
- 🔒 Поддержка webhook оплаты (Prodamus)
- 📊 Команды `/stats`, `/admin`, `/queue`, `/backend`

---

## 🛠 Установка на Render.com

1. **Форкните репозиторий** на GitHub
2. Зайдите на [https://render.com](https://render.com) и создайте новый **Web Service**
3. Подключите ваш форк как GitHub-репозиторий
4. В разделе **Environment → Environment Variables** добавьте:

TELEGRAM_BOT_TOKEN=ваш_токен_бота
ADMIN_USER_IDS=123456789,987654321
PAYDUS_WEBHOOK_SECRET=секрет_из_кабинета_Prodamus
PAYDUS_PRO_AMOUNT=299.0
PRO_USER_IDS=987654321
WHISPER_MODEL=base

go
Копировать код

5. Укажите команду запуска (если требуется вручную):  
```bash
python -m app.bot
Убедитесь, что Render определил порт 10000+ или отключите автоопределение портов, если сервис Telegram-бот (без web-сервера)

🧪 Локальный запуск (для разработчиков)
bash
Копировать код
pip install -r requirements.txt
python -m app.bot
📡 Webhook для Prodamus
Настройте в личном кабинете Prodamus:

arduino
Копировать код
https://your-service-name.onrender.com/webhook/prodamus
📦 Команды бота
Команда	Описание
/start	Приветствие и инструкция
/help	Как пользоваться ботом
/stats	Показывает текущие лимиты
/premium	Ссылка на оплату PRO
/queue	(админ) Статистика очереди
/admin	(админ) Панель администратора
/backend	(админ) Показ текущего AI-бэкенда и модели
/addpro ID	(админ) Добавить пользователя в PRO
/removepro ID	(админ) Удалить пользователя из PRO

🧠 Используемые технологии
Python 3.11+

python-telegram-bot

FastAPI (для webhook)

Redis / Postgres (для хранилища)

ffmpeg, pydub (обработка медиа)

ReportLab (PDF генерация)

🏁 Планы развития
Поддержка Google Drive и Dropbox

Анализ стиля речи / эмоций

Выгрузка в DOCX и JSON

Настройки для администратора

💬 Поддержка
Обратная связь: Telegram
Автор: AI-Vera Team

🔐 Лицензия
MIT License. Используйте свободно, но с умом 🙏


---

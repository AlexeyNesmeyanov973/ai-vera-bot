# Используем slim-базу, чтобы не тянуть лишнее
FROM python:3.10-slim

# --- Базовые настройки окружения ---
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # чтобы импортировалась папка ./app как пакет
    PYTHONPATH=/app

# --- Системные зависимости ---
# ffmpeg — для pydub/yt-dlp
# libgomp1 — OpenMP для ctranslate2/faster-whisper
# tini — корректная обработка сигналов (используется в ENTRYPOINT)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgomp1 \
    tini \
 && rm -rf /var/lib/apt/lists/*


# --- Рабочая директория ---
WORKDIR /app

# Сначала копируем только requirements.txt — это ускоряет билд за счёт кэша
COPY requirements.txt /app/requirements.txt

# Обновим pip/сетап-тулы и поставим зависимости проекта
RUN pip install --upgrade pip setuptools wheel \
 && pip install -r /app/requirements.txt

# Теперь копируем остальной код
COPY . /app

# Не обязателен, но полезен для web-сервиса
EXPOSE 8000

# По умолчанию запускаем через tini и выводим справку.
# На Render команду всё равно переопределяешь в render.yaml:
#  - для worker:  python -m app.bot
#  - для web:     gunicorn -w 2 -b 0.0.0.0:${PORT:-8000} app.web:app
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-c", "print('Image built. Override CMD via Render dockerCommand.')"]

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Системные пакеты: только то, что нужно для аудио и сборки колёс
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ffmpeg \
      git \
      build-essential \
      fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY app/ ./app/
RUN mkdir -p downloads && chmod 755 downloads

# Непривилегированный пользователь
RUN useradd --create-home --shell /bin/bash vera
USER vera

# Точка входа — только воркер
CMD ["python", "-m", "app.bot"]

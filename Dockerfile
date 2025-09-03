FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Системные пакеты
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ffmpeg \
      git \
      build-essential \
      fonts-dejavu-core \
      libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY app/ ./app/
RUN mkdir -p downloads

# Непривилегированный пользователь и права
RUN useradd --create-home --shell /bin/bash vera && \
    chown -R vera:vera /app
USER vera

CMD ["python", "-m", "app.bot"]

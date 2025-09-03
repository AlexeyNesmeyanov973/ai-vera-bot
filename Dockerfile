# Dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Если используешь requirements.txt
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Если у тебя pyproject.toml/poetry — ставь по нему (и удали блок выше)

COPY . /app

# Команда будет переопределена из render.yaml (dockerCommand)
CMD ["python", "-m", "app.bot"]

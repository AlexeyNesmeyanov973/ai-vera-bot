FROM python:3.11-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ffmpeg \
      git \
      build-essential \
      fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
RUN mkdir -p downloads && chmod 755 downloads

RUN useradd --create-home --shell /bin/bash vera
USER vera

# Переопределяется в render.yaml
CMD ["python", "-m", "app.bot"]

# ==============================================================================
# TG Digest System - Dockerfile
# ==============================================================================
FROM python:3.12-slim

LABEL maintainer="ASUDD Team"
LABEL description="Telegram Digest System for ASUDD"

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    # PostgreSQL client
    postgresql-client \
    # Tesseract OCR
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    # Полезные утилиты
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создание пользователя
RUN useradd -m -s /bin/bash appuser

# Рабочая директория
WORKDIR /app

# Копируем requirements первым для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY scripts/ /app/scripts/
COPY config/ /app/config/
COPY prompts/ /app/prompts/
COPY db/ /app/db/

# Создаём директории для данных
RUN mkdir -p /app/data /app/logs /app/media \
    && chown -R appuser:appuser /app

# Переключаемся на пользователя
USER appuser

# Переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_FILE=/app/config/channels.json \
    PROMPTS_DIR=/app/prompts \
    LOGS_DIR=/app/logs \
    MEDIA_DIR=/app/media \
    TG_SESSION_FILE=/app/data/telethon.session

# Healthcheck
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Точка входа
ENTRYPOINT ["python", "/app/scripts/digest_worker.py"]
CMD ["--interval", "30"]

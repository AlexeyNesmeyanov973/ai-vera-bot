# app/__init__.py
"""
AI-Vera Bot package init.
Собирает общие модули и предоставляет единые точки входа.
"""

import logging

# Не переопределяем логгинг, если он уже настроен где-то выше (gunicorn/uvicorn и т.п.)
_root = logging.getLogger()
if not _root.handlers:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )

# Экспортируем общий storage, чтобы было удобно:
#   from app import storage
from . import storage

__all__ = ["storage"]

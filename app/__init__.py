"""
AI-Vera Bot package init.
Собирает общие модули и предоставляет единые точки входа.
"""

import logging

# Базовая настройка логгера (если не переопределена в других местах)
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)

# Экспортируем общий storage, чтобы его удобно импортировать через `from app import storage`
from . import storage

__all__ = ["storage"]

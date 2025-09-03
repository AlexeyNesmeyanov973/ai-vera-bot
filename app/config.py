import os
from dotenv import load_dotenv

load_dotenv()

# === Telegram ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_IDS = [int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]

# === Prodamus (fallback) ===
PRODAMUS_WEBHOOK_SECRET = os.getenv("PRODAMUS_WEBHOOK_SECRET", "")
PRODAMUS_PAYMENT_LINK = os.getenv("PRODAMUS_PAYMENT_LINK", "")
PRODAMUS_PRO_AMOUNT = float(os.getenv("PRODAMUS_PRO_AMOUNT", "299.0"))

# === YooKassa (приоритетная, если задана) ===
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
YOOKASSA_RETURN_URL = os.getenv("YOOKASSA_RETURN_URL", "")
YOOKASSA_PRO_AMOUNT = float(os.getenv("YOOKASSA_PRO_AMOUNT", "299.0"))

# === Whisper ===
WHISPER_BACKEND = os.getenv("WHISPER_BACKEND", "faster")   # "faster" или "openai"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru")     # "ru" или "auto"

# === Лимиты ===
FREE_USER_DAILY_LIMIT_MINUTES = int(os.getenv("FREE_USER_DAILY_LIMIT_MINUTES", "30"))
PRO_USER_DAILY_LIMIT_MINUTES = int(os.getenv("PRO_USER_DAILY_LIMIT_MINUTES", "180"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "20"))          # лимит для файлов, присланных в Telegram
URL_MAX_FILE_SIZE_MB = int(os.getenv("URL_MAX_FILE_SIZE_MB", "2000")) # лимит для файлов, скачанных по ссылке

# === Сверх лимита (докупка) ===
OVERAGE_PRICE_RUB = float(os.getenv("OVERAGE_PRICE_RUB", "2.0"))

# === PRO пользователи (миграция при старте) ===
PRO_USER_IDS = [int(x) for x in os.getenv("PRO_USER_IDS", "").split(",") if x.strip()]

# === Опциональные сервисы ===
REDIS_URL = os.getenv("REDIS_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

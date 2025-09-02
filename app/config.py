import os
from dotenv import load_dotenv
from app.payment_manager import PaymentManager

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("ОШИБКА: Не найден TELEGRAM_BOT_TOKEN в переменных окружения.")

FREE_USER_DAILY_LIMIT_MINUTES = 30
PRO_USER_DAILY_LIMIT_MINUTES = 120
MAX_FILE_SIZE_MB = 20

ADMIN_USER_IDS = list(map(int, os.getenv('ADMIN_USER_IDS', '').split(','))) if os.getenv('ADMIN_USER_IDS') else []
PRO_USER_IDS = list(map(int, os.getenv('PRO_USER_IDS', '').split(','))) if os.getenv('PRO_USER_IDS') else []

# Бэкенд распознавания: "openai" | "faster"
WHISPER_BACKEND = os.getenv('WHISPER_BACKEND', 'faster').lower()
WHISPER_MODEL = os.getenv('WHISPER_MODEL', 'small')

# Paydmus
PAYDMUS_WEBHOOK_SECRET = os.getenv('PAYDMUS_WEBHOOK_SECRET', '')
PAYDMUS_PRO_AMOUNT = float(os.getenv('PAYDMUS_PRO_AMOUNT', '299.0'))

# Хранилища
REDIS_URL = os.getenv('REDIS_URL', '')
DATABASE_URL = os.getenv('DATABASE_URL', '')

payment_manager = None
if PAYDMUS_WEBHOOK_SECRET:
    payment_manager = PaymentManager(PAYDMUS_WEBHOOK_SECRET)
else:
    print("⚠️  PAYDMUS_WEBHOOK_SECRET не установлен. Платежи отключены.")

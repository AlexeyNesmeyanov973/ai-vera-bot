import os
from dotenv import load_dotenv

load_dotenv()

# ------- helpers -------
def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default)

def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)).strip())
    except Exception:
        return default

def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except Exception:
        return default

def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").strip().lower()
    if val in ("1", "true", "yes", "y", "on"):
        return True
    if val in ("0", "false", "no", "n", "off"):
        return False
    return default

def _env_list_int(key: str) -> list[int]:
    raw = os.getenv(key, "")
    out: list[int] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(int(p))
        except Exception:
            pass
    return out

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
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "20"))            # для файлов из Telegram
URL_MAX_FILE_SIZE_MB = int(os.getenv("URL_MAX_FILE_SIZE_MB", "2000"))  # по ссылке

# === Сверх лимита (докупка) ===
OVERAGE_PRICE_RUB = float(os.getenv("OVERAGE_PRICE_RUB", "2.0"))

# === PRO пользователи (миграция при старте) ===
PRO_USER_IDS = [int(x) for x in os.getenv("PRO_USER_IDS", "").split(",") if x.strip()]

# === Опциональные сервисы ===
REDIS_URL = os.getenv("REDIS_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# === Потоковая загрузка / временные файлы ===
TMP_DIR = os.getenv("TMP_DIR", "downloads")
STREAM_CHUNK_MB = float(os.getenv("STREAM_CHUNK_MB", "4"))
STREAM_TIMEOUT_S = int(os.getenv("STREAM_TIMEOUT_S", "45"))
RESUME_DOWNLOADS = int(os.getenv("RESUME_DOWNLOADS", "1"))
YTDLP_AUDIO_ONLY = int(os.getenv("YTDLP_AUDIO_ONLY", "1"))

# === Диаризация (опционально) ===
DIARIZATION_BACKEND = os.getenv("DIARIZATION_BACKEND", "none")  # "pyannote" | "none"
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")

# === Реферальная программа: базовые настройки ===
REF_ENABLED = os.getenv("REF_ENABLED", "0") in ("1", "true", "True", "yes", "YES")  # 1=вкл, 0=выкл
REF_BONUS_MINUTES = int(os.getenv("REF_BONUS_MINUTES", "10"))  # за первую удачную транскрибацию друга
REF_MAX_REWARDS_PER_REFERRER_PER_DAY = int(os.getenv("REF_MAX_REWARDS_PER_REFERRER_PER_DAY", "3"))
REF_TIERS = os.getenv("REF_TIERS", "3:1,5:3,10:7")  # строка, парсишь в боте

# === Реферальные «трофеи» (ВАУ): пороги → дни PRO ===
# Формат: "3:1,5:3,10:7"  (за 3 друзей — 1 день PRO; за 5 — 3 дня; за 10 — 7 дней)
REF_TIERS = os.getenv("REF_TIERS", "3:1,5:3,10:7")


# Необязательно: сттикеры для уведомлений о достижении порогов (по порядку)
# Пример: REF_TIER_STICKERS="CAACAgIAAxkBA...,CAACAgIAAxkBB..."
REF_TIER_STICKERS = [s for s in os.getenv("REF_TIER_STICKERS", "").split(",") if s.strip()]

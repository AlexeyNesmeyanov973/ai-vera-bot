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
TELEGRAM_BOT_TOKEN = _env_str("TELEGRAM_BOT_TOKEN", "")
ADMIN_USER_IDS = _env_list_int("ADMIN_USER_IDS")

# === Prodamus (fallback) ===
PRODAMUS_WEBHOOK_SECRET = _env_str("PRODAMUS_WEBHOOK_SECRET", "")
PRODAMUS_PAYMENT_LINK = _env_str("PRODAMUS_PAYMENT_LINK", "")
PRODAMUS_PRO_AMOUNT = _env_float("PRODAMUS_PRO_AMOUNT", 299.0)

# === YooKassa (приоритетная, если задана) ===
YOOKASSA_SHOP_ID = _env_str("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = _env_str("YOOKASSA_SECRET_KEY", "")
YOOKASSA_RETURN_URL = _env_str("YOOKASSA_RETURN_URL", "")
YOOKASSA_PRO_AMOUNT = _env_float("YOOKASSA_PRO_AMOUNT", 299.0)

# === Whisper ===
WHISPER_BACKEND = _env_str("WHISPER_BACKEND", "faster")  # "faster" | "openai"
WHISPER_MODEL = _env_str("WHISPER_MODEL", "small")
WHISPER_LANGUAGE = _env_str("WHISPER_LANGUAGE", "auto")  # "auto" по умолчанию

# === Лимиты ===
FREE_USER_DAILY_LIMIT_MINUTES = _env_int("FREE_USER_DAILY_LIMIT_MINUTES", 30)
PRO_USER_DAILY_LIMIT_MINUTES = _env_int("PRO_USER_DAILY_LIMIT_MINUTES", 180)
MAX_FILE_SIZE_MB = _env_int("MAX_FILE_SIZE_MB", 20)              # для файлов из Telegram
URL_MAX_FILE_SIZE_MB = _env_int("URL_MAX_FILE_SIZE_MB", 2000)    # по ссылке

# === Сверх лимита (докупка) ===
OVERAGE_PRICE_RUB = _env_float("OVERAGE_PRICE_RUB", 2.0)

# === PRO пользователи (миграция при старте) ===
PRO_USER_IDS = _env_list_int("PRO_USER_IDS")

# === Опциональные сервисы ===
REDIS_URL = _env_str("REDIS_URL", "")
DATABASE_URL = _env_str("DATABASE_URL", "")

# === Потоковая загрузка / временные файлы ===
TMP_DIR = _env_str("TMP_DIR", "downloads")
STREAM_CHUNK_MB = _env_float("STREAM_CHUNK_MB", 4.0)
STREAM_TIMEOUT_S = _env_int("STREAM_TIMEOUT_S", 45)
RESUME_DOWNLOADS = _env_int("RESUME_DOWNLOADS", 1)
YTDLP_AUDIO_ONLY = _env_int("YTDLP_AUDIO_ONLY", 1)

# === Диаризация (опционально) ===
DIARIZATION_BACKEND = _env_str("DIARIZATION_BACKEND", "none")  # "pyannote" | "none"
HUGGINGFACE_TOKEN = _env_str("HUGGINGFACE_TOKEN", "")

# === Реферальная программа ===
REF_ENABLED = _env_bool("REF_ENABLED", False)
REF_BONUS_MINUTES = _env_int("REF_BONUS_MINUTES", 10)  # бонус за 1-ю удачную транскрибацию друга
REF_MAX_REWARDS_PER_REFERRER_PER_DAY = _env_int("REF_MAX_REWARDS_PER_REFERRER_PER_DAY", 3)

# Пороговые награды (строкой — парсится в боте):
# Формат: "3:1,5:3,10:7" (за 3 друзей → 1 день PRO; за 5 → 3; за 10 → 7)
REF_TIERS = _env_str("REF_TIERS", "3:1,5:3,10:7")

# Необязательно: стикеры для уведомлений о достижении порогов (по порядку, соответствуют REF_TIERS)
# Пример: REF_TIER_STICKERS="CAACAgIAAxkBA...,CAACAgIAAxkBB..."
REF_TIER_STICKERS = [s for s in _env_str("REF_TIER_STICKERS", "").split(",") if s.strip()]

# (опционально) централизованное управление логами
LOG_LEVEL = _env_str("LOG_LEVEL", "INFO")

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
TELEGRAM_BOT_TOKEN = _env_str("TELEGRAM_BOT_TOKEN")
ADMIN_USER_IDS = _env_list_int("ADMIN_USER_IDS")
PRO_USER_IDS = _env_list_int("PRO_USER_IDS")  # миграция на старте

# === Prodamus (fallback) ===
PRODAMUS_WEBHOOK_SECRET = _env_str("PRODAMUS_WEBHOOK_SECRET")
PRODAMUS_PAYMENT_LINK   = _env_str("PRODAMUS_PAYMENT_LINK")
PRODAMUS_PRO_AMOUNT     = _env_float("PRODAMUS_PRO_AMOUNT", 299.0)

# === YooKassa (приоритетная, если задана) ===
YOOKASSA_SHOP_ID     = _env_str("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY  = _env_str("YOOKASSA_SECRET_KEY")
YOOKASSA_RETURN_URL  = _env_str("YOOKASSA_RETURN_URL")
YOOKASSA_PRO_AMOUNT  = _env_float("YOOKASSA_PRO_AMOUNT", 299.0)

# === Whisper ===
WHISPER_BACKEND   = _env_str("WHISPER_BACKEND", "faster")  # "faster" | "openai"
WHISPER_MODEL     = _env_str("WHISPER_MODEL", "small")
WHISPER_LANGUAGE  = _env_str("WHISPER_LANGUAGE", "auto")   # "auto" по умолчанию

# === Лимиты ===
FREE_USER_DAILY_LIMIT_MINUTES = _env_int("FREE_USER_DAILY_LIMIT_MINUTES", 30)
PRO_USER_DAILY_LIMIT_MINUTES  = _env_int("PRO_USER_DAILY_LIMIT_MINUTES", 180)
MAX_FILE_SIZE_MB              = _env_int("MAX_FILE_SIZE_MB", 49)       # TG файлы
URL_MAX_FILE_SIZE_MB          = _env_int("URL_MAX_FILE_SIZE_MB", 1024) # по ссылкам

# === Сверх лимита (докупка) ===
OVERAGE_PRICE_RUB = _env_float("OVERAGE_PRICE_RUB", 5.0)

# === Опциональные сервисы ===
REDIS_URL    = _env_str("REDIS_URL")
DATABASE_URL = _env_str("DATABASE_URL")

# === Временные файлы / потоковая загрузка ===
TMP_DIR            = _env_str("TMP_DIR", "downloads")
STREAM_CHUNK_MB    = _env_float("STREAM_CHUNK_MB", 4.0)
STREAM_TIMEOUT_S   = _env_int("STREAM_TIMEOUT_S", 45)
RESUME_DOWNLOADS   = _env_bool("RESUME_DOWNLOADS", True)
YTDLP_AUDIO_ONLY   = _env_bool("YTDLP_AUDIO_ONLY", True)

# === Диаризация (опционально) ===
DIARIZATION_BACKEND = _env_str("DIARIZATION_BACKEND", "none")  # "pyannote" | "none"
HUGGINGFACE_TOKEN   = _env_str("HUGGINGFACE_TOKEN")

# Удобные флаги наличия внешних сервисов
HAVE_REDIS = bool(REDIS_URL.strip())
HAVE_PG    = bool(DATABASE_URL.strip())

import logging
from app.config import PRO_USER_IDS
from app import storage

logger = logging.getLogger(__name__)

def run_startup_migrations():
    """
    Мигрируем PRO_USER_IDS из env в постоянное хранилище (Redis/Postgres).
    Идемпотентно: в Postgres используется ON CONFLICT DO NOTHING, в Redis — set.
    """
    try:
        if PRO_USER_IDS:
            migrated = 0
            for uid in PRO_USER_IDS:
                try:
                    storage.add_pro(int(uid))
                    migrated += 1
                except Exception as e:
                    logger.warning(f"Не удалось мигрировать user_id={uid}: {e}")
            logger.info(f"✅ Миграция PRO_USER_IDS завершена. Перенесено: {migrated}")
        else:
            logger.info("ℹ️ PRO_USER_IDS не задан — миграция не требуется.")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка миграции PRO_USER_IDS: {e}")

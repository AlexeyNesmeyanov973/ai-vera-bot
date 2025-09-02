from datetime import date
from app.config import FREE_USER_DAILY_LIMIT_MINUTES, PRO_USER_DAILY_LIMIT_MINUTES
from app import storage

class LimitManager:
    """
    Управление лимитами пользователей с персистентным хранилищем (Redis/Postgres/Memory).
    """
    def _get_user_limit_seconds(self, user_id: int) -> int:
        daily = PRO_USER_DAILY_LIMIT_MINUTES if storage.is_pro(user_id) else FREE_USER_DAILY_LIMIT_MINUTES
        return daily * 60

    def _ensure_today(self, user_id: int):
        used, last_date = storage.get_usage(user_id)
        today = date.today()
        if last_date != today:
            storage.set_usage(user_id, 0, today)

    def can_process(self, user_id: int, audio_duration_seconds: int) -> tuple[bool, str, int]:
        self._ensure_today(user_id)
        limit_s = self._get_user_limit_seconds(user_id)
        used_s, _ = storage.get_usage(user_id)
        remaining = limit_s - used_s
        if audio_duration_seconds > remaining:
            msg = (f"Превышен дневной лимит. Использовано: {used_s // 60} мин. "
                   f"Лимит: {limit_s // 60} мин. "
                   f"Не хватает: {(audio_duration_seconds - remaining) // 60} мин.")
            return False, msg, remaining
        return True, "", remaining - audio_duration_seconds

    def update_usage(self, user_id: int, additional_seconds: int):
        used_s, last_date = storage.get_usage(user_id)
        storage.set_usage(user_id, used_s + additional_seconds, last_date)

    def get_usage_info(self, user_id: int) -> str:
        self._ensure_today(user_id)
        limit_s = self._get_user_limit_seconds(user_id)
        used_s, _ = storage.get_usage(user_id)
        remaining = limit_s - used_s
        is_pro = storage.is_pro(user_id)
        return (f"Ваш статус: {'PRO 🤩' if is_pro else 'Бесплатный'}\n"
                f"Использовано сегодня: {used_s // 60} мин.\n"
                f"Осталось сегодня: {remaining // 60} мин.\n"
                f"Общий дневной лимит: {limit_s // 60} мин.")

limit_manager = LimitManager()

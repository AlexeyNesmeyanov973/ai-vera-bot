# app/limit_manager.py
from datetime import date
from math import ceil
from typing import Tuple

from app.config import FREE_USER_DAILY_LIMIT_MINUTES, PRO_USER_DAILY_LIMIT_MINUTES
from app import storage


class LimitManager:
    """
    Управление лимитами с учётом докупленных секунд на сегодня.
    """

    def _get_base_limit_seconds(self, user_id: int) -> int:
        daily = PRO_USER_DAILY_LIMIT_MINUTES if storage.is_pro(user_id) else FREE_USER_DAILY_LIMIT_MINUTES
        return int(daily) * 60

    def _ensure_today(self, user_id: int) -> None:
        used, last_date = storage.get_usage(user_id)
        today = date.today()
        if last_date != today:
            storage.set_usage(user_id, 0, today)
        # overage хранится со своей датой в storage; сброс делается там

    def can_process(self, user_id: int, audio_duration_seconds: int) -> Tuple[bool, str, int, int]:
        """
        Return:
          ok, message, remaining_total_seconds, deficit_seconds
        """
        self._ensure_today(user_id)
        base_limit = self._get_base_limit_seconds(user_id)
        used_s, _ = storage.get_usage(user_id)
        extra_s, last = storage.get_overage(user_id)
        if last != date.today():
            extra_s = 0

        remaining_total = max(0, base_limit - used_s) + max(0, extra_s)
        if audio_duration_seconds > remaining_total:
            deficit = audio_duration_seconds - remaining_total
            msg = (
                f"Превышен дневной лимит.\n"
                f"Использовано сегодня: {used_s // 60} мин.\n"
                f"База: {base_limit // 60} мин.\n"
                f"Докуплено: {extra_s // 60} мин.\n"
                f"Не хватает: {max(1, ceil(deficit / 60))} мин."
            )
            return False, msg, remaining_total, deficit
        return True, "", remaining_total, 0

    def update_usage(self, user_id: int, additional_seconds: int) -> None:
        """
        Сначала тратим базовый лимит, затем списываем из докупленных секунд.
        """
        used_s, last_date = storage.get_usage(user_id)
        base_limit = self._get_base_limit_seconds(user_id)
        extra_s, last_over = storage.get_overage(user_id)
        if last_over != date.today():
            extra_s = 0

        base_remaining = max(0, base_limit - used_s)
        consume_from_base = min(base_remaining, max(0, int(additional_seconds)))
        consume_from_overage = max(0, int(additional_seconds) - consume_from_base)

        storage.set_usage(user_id, used_s + consume_from_base, last_date)
        if consume_from_overage > 0:
            storage.consume_overage_seconds(user_id, consume_from_overage)

    def get_usage_info(self, user_id: int) -> str:
        self._ensure_today(user_id)
        base_limit = self._get_base_limit_seconds(user_id)
        used_s, _ = storage.get_usage(user_id)
        extra_s, last = storage.get_overage(user_id)
        if last != date.today():
            extra_s = 0
        remaining_total = max(0, base_limit - used_s) + max(0, extra_s)
        is_pro = storage.is_pro(user_id)
        return (
            f"Ваш статус: {'PRO 🤩' if is_pro else 'Бесплатный'}\n"
            f"Использовано сегодня: {used_s // 60} мин.\n"
            f"Докуплено на сегодня: {extra_s // 60} мин.\n"
            f"Осталось сегодня (всего): {remaining_total // 60} мин.\n"
            f"Базовый дневной лимит: {base_limit // 60} мин."
        )


# Экспортируем инстанс
limit_manager = LimitManager()

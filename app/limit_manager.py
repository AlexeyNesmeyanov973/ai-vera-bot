import time
from datetime import datetime, timezone, timedelta
from app.config import FREE_USER_DAILY_LIMIT_MINUTES, PRO_USER_DAILY_LIMIT_MINUTES, PRO_USER_IDS

class LimitManager:
    """
    Класс для управления лимитами пользователей.
    Ведет учет использованных минут транскрибации за текущий день.
    """
    
    def __init__(self):
        # Словарь для хранения данных о пользователях: {user_id: {'used_seconds': 300, 'last_reset_date': '2023-11-30'}}
        self.user_data = {}
        self._reset_old_entries()

    def _get_user_limit_seconds(self, user_id: int) -> int:
        """Возвращает дневной лимит пользователя в секундах."""
        daily_limit_minutes = PRO_USER_DAILY_LIMIT_MINUTES if user_id in PRO_USER_IDS else FREE_USER_DAILY_LIMIT_MINUTES
        return daily_limit_minutes * 60

    def _get_today_date_str(self) -> str:
        """Возвращает сегодняшнюю дату в виде строки YYYY-MM-DD."""
        return datetime.now(timezone.utc).strftime('%Y-%m-%d')

    def _reset_old_entries(self):
        """Сбрасывает счетчики, если дата последнего использования не сегодня."""
        today = self._get_today_date_str()
        for user_id, data in list(self.user_data.items()):
            if data.get('last_reset_date') != today:
                self.user_data[user_id] = {'used_seconds': 0, 'last_reset_date': today}

    def _ensure_user_entry(self, user_id: int):
        """Создает запись для пользователя, если ее нет, или сбрасывает, если день сменился."""
        today = self._get_today_date_str()
        if user_id not in self.user_data or self.user_data[user_id].get('last_reset_date') != today:
            self.user_data[user_id] = {'used_seconds': 0, 'last_reset_date': today}

    def can_process(self, user_id: int, audio_duration_seconds: int) -> tuple[bool, str, int]:
        """
        Проверяет, может ли пользователь обработать аудио указанной длительности.
        
        Args:
            user_id: ID пользователя в Telegram.
            audio_duration_seconds: Длительность аудио в секундах.
            
        Returns:
            tuple: (Можно ли обработать, Сообщение об ошибке, Оставшееся количество секунд)
        """
        self._ensure_user_entry(user_id)
        
        user_limit_seconds = self._get_user_limit_seconds(user_id)
        used_seconds = self.user_data[user_id]['used_seconds']
        remaining_seconds = user_limit_seconds - used_seconds
        
        if audio_duration_seconds > remaining_seconds:
            message = (f"Превышен дневной лимит. Использовано: {used_seconds // 60} мин. "
                      f"Лимит: {user_limit_seconds // 60} мин. "
                      f"Для обработки этого файла не хватает {(audio_duration_seconds - remaining_seconds) // 60} мин.")
            return (False, message, remaining_seconds)
        
        return (True, "", remaining_seconds - audio_duration_seconds)

    def update_usage(self, user_id: int, additional_seconds: int):
        """
        Обновляет счетчик использованных секунд для пользователя.
        
        Args:
            user_id: ID пользователя в Telegram.
            additional_seconds: Секунды, которые нужно добавить к счетчику.
        """
        self._ensure_user_entry(user_id)
        self.user_data[user_id]['used_seconds'] += additional_seconds

    def get_usage_info(self, user_id: int) -> str:
        """
        Возвращает текстовую строку с информацией о использовании лимита.
        
        Args:
            user_id: ID пользователя в Telegram.
            
        Returns:
            str: Информация о лимитах.
        """
        self._ensure_user_entry(user_id)
        
        user_limit_seconds = self._get_user_limit_seconds(user_id)
        used_seconds = self.user_data[user_id]['used_seconds']
        remaining_seconds = user_limit_seconds - used_seconds
        is_pro = user_id in PRO_USER_IDS
        
        return (f"Ваш статус: {'PRO 🤩' if is_pro else 'Бесплатный'}\n"
                f"Использовано сегодня: {used_seconds // 60} мин.\n"
                f"Осталось сегодня: {remaining_seconds // 60} мин.\n"
                f"Общий дневной лимит: {user_limit_seconds // 60} мин.")

# Создаем глобальный экземпляр менеджера лимитов
limit_manager = LimitManager()
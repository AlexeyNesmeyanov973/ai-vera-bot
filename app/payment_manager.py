import logging
import hmac
import hashlib
import json
from typing import Dict, List
from app.config import PRO_USER_IDS
from app.limit_manager import limit_manager

logger = logging.getLogger(__name__)

class PaymentManager:
    """Класс для управления платежами через Paydmus."""
    
    def __init__(self, webhook_secret: str):
        self.webhook_secret = webhook_secret.encode('utf-8')
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Проверяет подпись webhook от Paydmus.
        
        Args:
            payload: Тело запроса
            signature: Подпись из заголовка
            
        Returns:
            bool: True если подпись верна
        """
        try:
            expected_signature = hmac.new(
                self.webhook_secret, 
                payload, 
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(expected_signature, signature)
        except Exception as e:
            logger.error(f"Ошибка проверки подписи: {e}")
            return False
    
    async def handle_webhook(self, payload: Dict) -> Dict:
        """
        Обрабатывает webhook от Paydmus.
        
        Args:
            payload: Данные от Paydmus
            
        Returns:
            Dict: Результат обработки
        """
        try:
            event_type = payload.get('event')
            order_id = payload.get('order', {}).get('id')
            user_id = payload.get('order', {}).get('user_id')
            
            if not user_id:
                return {'success': False, 'error': 'No user_id'}
            
            user_id = int(user_id)
            
            if event_type == 'order.paid':
                # Добавляем пользователя в PRO
                if user_id not in PRO_USER_IDS:
                    PRO_USER_IDS.append(user_id)
                
                logger.info(f"Пользователь {user_id} получил PRO статус. Заказ: {order_id}")
                return {'success': True, 'message': f'User {user_id} upgraded to PRO'}
            
            elif event_type == 'order.refunded':
                # Убираем PRO статус
                if user_id in PRO_USER_IDS:
                    PRO_USER_IDS.remove(user_id)
                
                logger.info(f"Пользователь {user_id} потерял PRO статус. Заказ: {order_id}")
                return {'success': True, 'message': f'User {user_id} downgraded from PRO'}
            
            return {'success': False, 'error': 'Unknown event type'}
            
        except Exception as e:
            logger.error(f"Ошибка обработки webhook: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_payment_url(self, user_id: int, amount: float = 299.0) -> str:
        """
        Генерирует URL для оплаты через Paydmus.
        
        Args:
            user_id: ID пользователя
            amount: Сумма оплаты
            
        Returns:
            str: URL для оплаты
        """
        base_url = "https://paydmus.com/api/v1/payment"
        return f"{base_url}?user_id={user_id}&amount={amount}&product_name=AI+Vera+PRO"

# Глобальный экземпляр (инициализируется в config.py)
payment_manager = None
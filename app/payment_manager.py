import logging
import hmac
import hashlib
from typing import Dict
from app import storage

logger = logging.getLogger(__name__)

class PaymentManager:
    """Paydmus."""
    def __init__(self, webhook_secret: str):
        self.webhook_secret = webhook_secret.encode('utf-8')

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        try:
            expected = hmac.new(self.webhook_secret, payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception as e:
            logger.error(f"Ошибка проверки подписи: {e}")
            return False

    async def handle_webhook(self, payload: Dict) -> Dict:
        try:
            event = payload.get('event')
            order = payload.get('order', {})
            user_id = order.get('user_id')
            if not user_id:
                return {'success': False, 'error': 'No user_id'}

            user_id = int(user_id)

            if event == 'order.paid':
                storage.add_pro(user_id)
                logger.info(f"User {user_id} upgraded to PRO")
                return {'success': True, 'message': f'User {user_id} upgraded to PRO'}

            elif event == 'order.refunded':
                storage.remove_pro(user_id)
                logger.info(f"User {user_id} downgraded from PRO")
                return {'success': True, 'message': f'User {user_id} downgraded from PRO'}

            return {'success': False, 'error': 'Unknown event type'}
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return {'success': False, 'error': str(e)}

    def get_payment_url(self, user_id: int, amount: float = 299.0) -> str:
        base_url = "https://paydmus.com/api/v1/payment"
        return f"{base_url}?user_id={user_id}&amount={amount}&product_name=AI+Vera+PRO"

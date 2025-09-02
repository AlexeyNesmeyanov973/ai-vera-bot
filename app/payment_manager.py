import logging
import hmac
import hashlib
import json
from typing import Dict, Optional
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from app import storage

logger = logging.getLogger(__name__)

class PaymentManager:
    """
    Интеграция с Prodamus:
    - Проверка подписи вебхука: HMAC-SHA256(raw_body, secret) == signature_header
    - Ссылка на оплату: используем готовую ссылку из кабинета и добавляем ?user_id=...
    """

    # Подпись приходит "в заголовках запроса" (официальная справка Prodamus).
    # Точное имя может различаться в интеграциях; поддержим несколько вариантов.
    SIGNATURE_HEADER_CANDIDATES = [
        "X-Prodamus-Signature",
        "X-Signature",
        "Signature",
        "X-Pay-Signature",
    ]

    def __init__(self, webhook_secret: str, payment_link_base: Optional[str], default_amount: float = 299.0):
        self.webhook_secret = webhook_secret.encode("utf-8")
        self.payment_link_base = payment_link_base
        self.default_amount = default_amount

    def _extract_signature(self, headers: Dict[str, str]) -> Optional[str]:
        for key in self.SIGNATURE_HEADER_CANDIDATES:
            # Flask lowercases keys in request.headers? — это dict-like, но лучше пробовать точное имя
            if key in headers:
                return headers.get(key)
            # попробовать без учёта регистра
            for hk, hv in headers.items():
                if hk.lower() == key.lower():
                    return hv
        return None

    def verify_webhook_signature(self, raw_payload: bytes, headers: Dict[str, str]) -> bool:
        try:
            signature = self._extract_signature(headers)
            if not signature:
                logger.warning("Подпись вебхука отсутствует в заголовках")
                return False
            expected = hmac.new(self.webhook_secret, raw_payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception as e:
            logger.error(f"Ошибка проверки подписи: {e}")
            return False

    def _extract_user_id(self, payload: Dict) -> Optional[int]:
        """
        В Prodamus можно вернуть 'user_id' в вебхук через параметры ссылки/кастомные поля.
        Пробуем популярные места.
        """
        candidates = [
            payload.get("user_id"),
            (payload.get("order") or {}).get("user_id"),
            # иногда платформы заворачивают поля:
            ((payload.get("custom_fields") or {}).get("user_id") if isinstance(payload.get("custom_fields"), dict) else None),
            ((payload.get("params") or {}).get("user_id") if isinstance(payload.get("params"), dict) else None),
            ((payload.get("client") or {}).get("user_id") if isinstance(payload.get("client"), dict) else None),
        ]
        for v in candidates:
            if v is None:
                continue
            try:
                return int(v)
            except Exception:
                pass
        return None

    async def handle_webhook(self, payload: Dict) -> Dict:
        """
        Универсальная обработка:
        - успешная оплата -> add_pro
        - возврат/отмена -> remove_pro
        Ожидаем в payload что-то вроде:
          { "status": "success", ... }  или  { "event": "payment.succeeded", ... }
        """
        try:
            user_id = self._extract_user_id(payload)
            if not user_id:
                return {"success": False, "error": "No user_id in webhook payload"}

            event = (payload.get("event") or "").lower()
            status = (payload.get("status") or "").lower()

            paid = (status in ("success", "paid", "succeeded")) or ("paid" in event or "succeed" in event)
            refunded = (status in ("refund", "refunded")) or ("refund" in event)

            if paid:
                storage.add_pro(user_id)
                logger.info(f"User {user_id} upgraded to PRO (Prodamus webhook)")
                return {"success": True, "message": f"User {user_id} upgraded to PRO"}

            if refunded:
                storage.remove_pro(user_id)
                logger.info(f"User {user_id} downgraded from PRO (refund)")
                return {"success": True, "message": f"User {user_id} downgraded from PRO"}

            # Если событие неизвестно, просто логируем
            logger.info(f"Получен вебхук Prodamus (без изменений статуса): event={event}, status={status}")
            return {"success": True, "message": "Webhook received"}

        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return {"success": False, "error": str(e)}

    de

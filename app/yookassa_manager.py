import logging
from typing import Dict, Optional

from yookassa import Configuration, Payment
from app import storage

logger = logging.getLogger(__name__)

class YooKassaManager:
    """
    Интеграция с YooKassa.
    - Создаём платёж -> получаем confirmation_url (редирект)
    - В вебхуке перепроверяем статус через API (Payment.find_one)
    - user_id храним в metadata
    """

    def __init__(self, shop_id: str, secret_key: str, return_url: str, default_amount: float = 299.0):
        self.default_amount = default_amount
        self.return_url = return_url
        Configuration.account_id = shop_id
        Configuration.secret_key = secret_key
        logger.info("✅ YooKassa configured")

    def get_payment_url(self, user_id: int, amount: Optional[float] = None) -> str:
        amt = amount if amount is not None else self.default_amount
        description = f"AI-Vera PRO for user {user_id}"

        payment = Payment.create({
            "amount": {"value": f"{amt:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self.return_url or "https://t.me"},
            "description": description,
            "metadata": {"user_id": str(user_id), "product": "AI-Vera PRO"},
        })

        url = payment.confirmation.confirmation_url
        if not url:
            raise RuntimeError("YooKassa: confirmation_url is empty")
        return url

    async def handle_webhook(self, payload: Dict) -> Dict:
        try:
            obj = payload.get("object") or {}
            payment_id = obj.get("id")
            if not payment_id:
                return {"success": False, "error": "No payment id in webhook"}

            payment = Payment.find_one(payment_id)
            status = getattr(payment, "status", None)

            user_id_raw = None
            try:
                user_id_raw = payment.metadata.get("user_id") if payment.metadata else None
            except Exception:
                pass
            if not user_id_raw:
                user_id_raw = ((obj.get("metadata") or {}).get("user_id"))

            if not user_id_raw:
                return {"success": False, "error": "No user_id in metadata"}

            user_id = int(user_id_raw)

            if status == "succeeded":
                storage.add_pro(user_id)
                logger.info(f"YooKassa: user {user_id} upgraded to PRO (payment {payment_id})")
                return {"success": True, "message": f"user {user_id} upgraded to PRO"}
            elif status in ("canceled", "cancelled"):
                storage.remove_pro(user_id)
                logger.info(f"YooKassa: user {user_id} downgraded from PRO (canceled {payment_id})")
                return {"success": True, "message": f"user {user_id} downgraded from PRO"}
            else:
                logger.info(f"YooKassa webhook received, status={status} (no change)")
                return {"success": True, "message": "Webhook received, no change"}

        except Exception as e:
            logger.exception("YooKassa webhook error")
            return {"success": False, "error": str(e)}

import logging
from typing import Dict, Optional

from yookassa import Configuration, Payment
from app import storage

logger = logging.getLogger(__name__)

class YooKassaManager:
    """
    Интеграция с YooKassa.
    - PRO: create_pro_payment(get_payment_url)
    - TOPUP: create_topup_payment(minutes)
    - В вебхуке перепроверяем статус через API и применяем (upgrade PRO / add overage)
    """

    def __init__(self, shop_id: str, secret_key: str, return_url: str, default_amount: float = 299.0):
        self.default_amount = default_amount
        self.return_url = return_url
        Configuration.account_id = shop_id
        Configuration.secret_key = secret_key
        logger.info("✅ YooKassa configured")

    # === PRO (как раньше) ===
    def get_payment_url(self, user_id: int, amount: Optional[float] = None) -> str:
        amt = amount if amount is not None else self.default_amount
        description = f"AI-Vera PRO for user {user_id}"
        payment = Payment.create({
            "amount": {"value": f"{amt:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self.return_url or "https://t.me"},
            "description": description,
            "metadata": {"user_id": str(user_id), "product": "AI-Vera PRO", "type": "pro"},
        })
        url = payment.confirmation.confirmation_url
        if not url:
            raise RuntimeError("YooKassa: confirmation_url is empty")
        return url

    # === TOPUP (докупка минут) ===
    def get_topup_url(self, user_id: int, minutes: int, amount: float) -> str:
        description = f"AI-Vera Topup {minutes}m for user {user_id}"
        payment = Payment.create({
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self.return_url or "https://t.me"},
            "description": description,
            "metadata": {"user_id": str(user_id), "type": "topup", "minutes": str(int(minutes))},
        })
        url = payment.confirmation.confirmation_url
        if not url:
            raise RuntimeError("YooKassa: confirmation_url is empty (topup)")
        return url

    async def handle_webhook(self, payload: Dict) -> Dict:
        try:
            obj = payload.get("object") or {}
            payment_id = obj.get("id")
            if not payment_id:
                return {"success": False, "error": "No payment id in webhook"}

            payment = Payment.find_one(payment_id)
            status = getattr(payment, "status", None)

            meta = {}
            try:
                meta = payment.metadata or {}
            except Exception:
                pass
            if not meta:
                meta = obj.get("metadata") or {}

            user_id_raw = meta.get("user_id")
            if not user_id_raw:
                return {"success": False, "error": "No user_id in metadata"}

            user_id = int(user_id_raw)
            pay_type = (meta.get("type") or "").lower()

            if status == "succeeded":
                if pay_type == "topup":
                    minutes = int(meta.get("minutes") or "0")
                    if minutes > 0:
                        storage.add_overage_seconds(user_id, minutes * 60)
                        logger.info(f"YooKassa: user {user_id} TOPUP +{minutes}m (payment {payment_id})")
                        return {"success": True, "message": f"user {user_id} topped up {minutes}m"}
                    return {"success": True, "message": "topup succeeded, but minutes=0"}
                # default → PRO
                storage.add_pro(user_id)
                logger.info(f"YooKassa: user {user_id} upgraded to PRO (payment {payment_id})")
                return {"success": True, "message": f"user {user_id} upgraded to PRO"}
            elif status in ("canceled", "cancelled"):
                logger.info(f"YooKassa: payment canceled ({payment_id})")
                return {"success": True, "message": "payment canceled"}
            else:
                logger.info(f"YooKassa webhook received, status={status} (no change)")
                return {"success": True, "message": "Webhook received, no change"}

        except Exception as e:
            logger.exception("YooKassa webhook error")
            return {"success": False, "error": str(e)}

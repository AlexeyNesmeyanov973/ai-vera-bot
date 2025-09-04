# app/yookassa_manager.py
import logging
from typing import Dict, Optional

from yookassa import Configuration, Payment
from app import storage

logger = logging.getLogger(__name__)


class YooKassaManager:
    """
    Интеграция с YooKassa:
      - get_payment_url(user_id, amount?)        -> URL на оплату PRO
      - get_topup_url(user_id, minutes, amount)  -> URL на оплату докупки минут
      - async handle_webhook(payload)            -> Dict (idempotent)
    """

    def __init__(self, shop_id: str, secret_key: str, return_url: str, default_amount: float = 299.0):
        self.default_amount = float(default_amount or 0.0)
        self.return_url = return_url or "https://t.me"
        Configuration.account_id = str(shop_id)
        Configuration.secret_key = str(secret_key)
        logger.info("✅ YooKassa configured")

    # === PRO ===
    def get_payment_url(self, user_id: int, amount: Optional[float] = None) -> str:
        amt = float(amount if amount is not None else self.default_amount)
        description = f"AI-Vera PRO for user {user_id}"
        payment = Payment.create({
            "amount": {"value": f"{amt:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self.return_url},
            "description": description,
            "metadata": {"user_id": str(user_id), "product": "AI-Vera PRO", "type": "pro"},
        })
        url = getattr(payment, "confirmation", None).confirmation_url
        if not url:
            raise RuntimeError("YooKassa: confirmation_url is empty")
        return url

    # === TOPUP ===
    def get_topup_url(self, user_id: int, minutes: int, amount: float) -> str:
        amt = float(amount)
        mins = int(minutes)
        description = f"AI-Vera Topup {mins}m for user {user_id}"
        payment = Payment.create({
            "amount": {"value": f"{amt:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self.return_url},
            "description": description,
            "metadata": {"user_id": str(user_id), "type": "topup", "minutes": str(mins)},
        })
        url = getattr(payment, "confirmation", None).confirmation_url
        if not url:
            raise RuntimeError("YooKassa: confirmation_url is empty (topup)")
        return url

    # === Webhook ===
    async def handle_webhook(self, payload: Dict) -> Dict:
        """
        Идемпотентная обработка webhook:
          • проверяем payment_id
          • уточняем статус через API
          • применяем PRO / TOPUP
        """
        try:
            obj = payload.get("object") or {}
            payment_id = obj.get("id")
            if not payment_id:
                return {"success": False, "error": "No payment id in webhook"}

            if storage.is_payment_processed("yookassa", payment_id):
                logger.info("YooKassa: duplicate webhook ignored (%s)", payment_id)
                return {"success": True, "message": "already processed"}

            payment = Payment.find_one(payment_id)
            status = getattr(payment, "status", None)

            # metadata (из API приоритезируем; если нет — берём из payload.object.metadata)
            meta = {}
            try:
                meta = payment.metadata or {}
            except Exception:
                meta = {}
            if not meta:
                meta = obj.get("metadata") or {}

            user_id_raw = meta.get("user_id")
            if not user_id_raw:
                return {"success": False, "error": "No user_id in metadata"}
            try:
                user_id = int(user_id_raw)
            except Exception:
                return {"success": False, "error": "Invalid user_id in metadata"}

            pay_type = str(meta.get("type") or "").lower()

            if status == "succeeded":
                # помечаем как обработанный ДО побочных действий
                storage.mark_payment_processed("yookassa", payment_id)

                if pay_type == "topup":
                    try:
                        minutes = int(meta.get("minutes") or "0")
                    except Exception:
                        minutes = 0
                    if minutes > 0:
                        storage.add_overage_seconds(user_id, minutes * 60)
                        logger.info("YooKassa: user %s TOPUP +%sm (payment %s)", user_id, minutes, payment_id)
                        return {"success": True, "message": f"user {user_id} topped up {minutes}m"}
                    return {"success": True, "message": "topup succeeded, but minutes=0"}

                # default: PRO
                storage.add_pro(user_id)
                logger.info("YooKassa: user %s upgraded to PRO (payment %s)", user_id, payment_id)
                return {"success": True, "message": f"user {user_id} upgraded to PRO"}

            elif status in ("canceled", "cancelled"):
                logger.info("YooKassa: payment canceled (%s)", payment_id)
                return {"success": True, "message": "payment canceled"}

            else:
                logger.info("YooKassa webhook received, status=%s (no change)", status)
                return {"success": True, "message": "Webhook received, no change"}

        except Exception as e:
            logger.exception("YooKassa webhook error")
            return {"success": False, "error": str(e)}

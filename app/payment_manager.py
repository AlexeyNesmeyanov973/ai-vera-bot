import logging
import hmac
import hashlib
from typing import Dict, Optional
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from app import storage

logger = logging.getLogger(__name__)

class PaymentManager:
    """
    Prodamus:
    - verify_webhook_signature
    - get_payment_url (PRO)
    - get_topup_url (докупка минут)
    - handle_webhook: применяет PRO или докупку, если пришли метаданные
    """

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
            if key in headers:
                return headers.get(key)
            for hk, hv in headers.items():
                if hk.lower() == key.lower():
                    return hv
        return None

    def verify_webhook_signature(self, raw_payload: bytes, headers: Dict[str, str]) -> bool:
        try:
            signature = self._extract_signature(headers)
            if not signature:
                logger.warning("Prodamus: подпись вебхука отсутствует")
                return False
            expected = hmac.new(self.webhook_secret, raw_payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception as e:
            logger.error(f"Prodamus: ошибка проверки подписи: {e}")
            return False

    def _extract_user_id(self, payload: Dict) -> Optional[int]:
        candidates = [
            payload.get("user_id"),
            (payload.get("order") or {}).get("user_id"),
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

    def _extract_minutes(self, payload: Dict) -> int:
        """Пытаемся найти minutes в разных местах (params/custom_fields)."""
        paths = [
            ("params", "minutes"),
            ("custom_fields", "minutes"),
            ("order", "minutes"),
        ]
        for p, k in paths:
            d = payload.get(p) or {}
            if isinstance(d, dict) and k in d:
                try:
                    return int(d[k])
                except Exception:
                    pass
        return 0

    def _append_query(self, base_url: str, extra: Dict[str, str]) -> str:
        url = urlparse(base_url)
        q = dict(parse_qsl(url.query, keep_blank_values=True))
        q.update({k: str(v) for k, v in extra.items()})
        new_query = urlencode(q)
        return urlunparse((url.scheme, url.netloc, url.path, url.params, new_query, url.fragment))

    # === PRO ===
    def get_payment_url(self, user_id: int, amount: Optional[float] = None) -> str:
        amt = amount if amount is not None else self.default_amount
        if self.payment_link_base:
            return self._append_query(self.payment_link_base, {"user_id": user_id, "amount": f"{amt:.2f}", "type": "pro"})
        return f"https://payform.prodamus.ru/?user_id={user_id}&amount={amt:.2f}&type=pro"

    # === TOPUP ===
    def get_topup_url(self, user_id: int, minutes: int, amount: float) -> str:
        if self.payment_link_base:
            return self._append_query(self.payment_link_base, {
                "user_id": user_id, "amount": f"{amount:.2f}", "type": "topup", "minutes": str(int(minutes))
            })
        return f"https://payform.prodamus.ru/?user_id={user_id}&amount={amount:.2f}&type=topup&minutes={int(minutes)}"

    async def handle_webhook(self, payload: Dict) -> Dict:
        try:
            user_id = self._extract_user_id(payload)
            if not user_id:
                return {"success": False, "error": "No user_id in webhook payload"}

            event = (payload.get("event") or "").lower()
            status = (payload.get("status") or "").lower()

            pay_type = ""
            for path in ("params", "custom_fields", "order"):
                d = payload.get(path) or {}
                if isinstance(d, dict):
                    pay_type = (d.get("type") or pay_type)
            pay_type = (pay_type or "").lower()

            minutes = self._extract_minutes(payload)

            paid = (status in ("success", "paid", "succeeded")) or ("paid" in event or "succeed" in event)
            if paid:
                if pay_type == "topup" and minutes > 0:
                    storage.add_overage_seconds(user_id, minutes * 60)
                    logger.info(f"Prodamus: user {user_id} TOPUP +{minutes}m")
                    return {"success": True, "message": f"User {user_id} topped up {minutes}m"}
                # default → PRO
                storage.add_pro(user_id)
                logger.info(f"Prodamus: user {user_id} upgraded to PRO")
                return {"success": True, "message": f"User {user_id} upgraded to PRO"}

            refunded = (status in ("refund", "refunded")) or ("refund" in event)
            if refunded:
                # докупку откатывать не будем (обычно не делают), но можно реализовать при желании
                logger.info(f"Prodamus: refund event for user {user_id}")
                return {"success": True, "message": "refund processed (no change)"}

            logger.info(f"Prodamus: webhook received (no change): event={event}, status={status}")
            return {"success": True, "message": "Webhook received"}
        except Exception as e:
            logger.error(f"Prodamus webhook error: {e}")
            return {"success": False, "error": str(e)}

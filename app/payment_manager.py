# app/payment_manager.py
import logging
import hmac
import hashlib
from typing import Dict, Optional, Any
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from app import storage

logger = logging.getLogger(__name__)


class PaymentManager:
    """
    Prodamus:
      - verify_webhook_signature(raw_payload, headers) -> bool
      - get_payment_url(user_id, amount?)
      - get_topup_url(user_id, minutes, amount)
      - handle_webhook(payload) -> Dict (idempotent)
    """

    SIGNATURE_HEADER_CANDIDATES = [
        "X-Prodamus-Signature",
        "X-Signature",
        "Signature",
        "X-Pay-Signature",
    ]

    def __init__(self, webhook_secret: str, payment_link_base: Optional[str], default_amount: float = 299.0):
        self.webhook_secret = (webhook_secret or "").encode("utf-8")
        self.payment_link_base = payment_link_base
        self.default_amount = float(default_amount or 0.0)

    # ----------------- helpers -----------------

    def _extract_signature(self, headers: Dict[str, str]) -> Optional[str]:
        if not headers:
            return None
        # прямое имя
        for k in self.SIGNATURE_HEADER_CANDIDATES:
            if k in headers:
                return headers.get(k)
        # без учёта регистра
        lower = {k.lower(): v for k, v in headers.items()}
        for k in self.SIGNATURE_HEADER_CANDIDATES:
            v = lower.get(k.lower())
            if v:
                return v
        return None

    @staticmethod
    def _normalize_signature(sig: str) -> str:
        s = (sig or "").strip().strip('"').strip("'")
        if s.lower().startswith("sha256="):
            s = s[7:]
        return s.lower()

    @staticmethod
    def _safe_int(v: Any) -> Optional[int]:
        try:
            return int(v)
        except Exception:
            return None

    def _extract_user_id(self, payload: Dict) -> Optional[int]:
        """Пробуем вытащить user_id из разных мест (и вложенных структур)."""
        cands = [
            payload.get("user_id"),
            (payload.get("order") or {}).get("user_id"),
            (payload.get("client") or {}).get("user_id"),
            (payload.get("customer") or {}).get("user_id"),
            (payload.get("metadata") or {}).get("user_id"),
            (payload.get("custom_fields") or {}).get("user_id") if isinstance(payload.get("custom_fields"), dict) else None,
            (payload.get("params") or {}).get("user_id") if isinstance(payload.get("params"), dict) else None,
            # запасные (часто кладут просто id внутрь вложений)
            (payload.get("user") or {}).get("id") if isinstance(payload.get("user"), dict) else None,
            (payload.get("customer") or {}).get("id") if isinstance(payload.get("customer"), dict) else None,
        ]
        for v in cands:
            v_int = self._safe_int(v)
            if v_int is not None:
                return v_int
        return None

    def _extract_minutes(self, payload: Dict) -> int:
        """Ищем minutes в распространённых местах."""
        for key in ("params", "custom_fields", "order", "metadata"):
            d = payload.get(key) or {}
            if isinstance(d, dict) and "minutes" in d:
                v = self._safe_int(d.get("minutes"))
                if v is not None and v > 0:
                    return v
        return 0

    def _extract_payment_id(self, payload: Dict) -> Optional[str]:
        """ID платежа/счёта/транзакции (для идемпотентности)."""
        cands = [
            payload.get("id"),
            payload.get("payment_id"),
            payload.get("invoice_id"),
            payload.get("transaction_id"),
            payload.get("uuid"),
            payload.get("hash"),
            (payload.get("order") or {}).get("id"),
            (payload.get("order") or {}).get("uid"),
            (payload.get("invoice") or {}).get("id"),
            (payload.get("payment") or {}).get("id"),
        ]
        for v in cands:
            if v:
                return str(v)
        return None

    def _append_query(self, base_url: str, extra: Dict[str, str]) -> str:
        url = urlparse(base_url)
        q = dict(parse_qsl(url.query, keep_blank_values=True))
        q.update({k: str(v) for k, v in extra.items()})
        new_query = urlencode(q)
        return urlunparse((url.scheme, url.netloc, url.path, url.params, new_query, url.fragment))

    # ----------------- public API -----------------

    def verify_webhook_signature(self, raw_payload: bytes, headers: Dict[str, str]) -> bool:
        """HMAC-SHA256 по «сырым» байтам тела запроса. Возвращает True/False."""
        try:
            sig = self._extract_signature(headers)
            if not sig:
                logger.warning("Prodamus: подпись отсутствует")
                return False
            expected = hmac.new(self.webhook_secret, raw_payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, self._normalize_signature(sig))
        except Exception as e:
            logger.error("Prodamus: ошибка проверки подписи: %s", e)
            return False

    # === PRO ===
    def get_payment_url(self, user_id: int, amount: Optional[float] = None) -> str:
        amt = float(amount if amount is not None else self.default_amount)
        if self.payment_link_base:
            return self._append_query(self.payment_link_base, {
                "user_id": user_id,
                "amount": f"{amt:.2f}",
                "type": "pro",
            })
        return f"https://payform.prodamus.ru/?user_id={user_id}&amount={amt:.2f}&type=pro"

    # === TOPUP ===
    def get_topup_url(self, user_id: int, minutes: int, amount: float) -> str:
        amt = float(amount)
        mins = int(minutes)
        if self.payment_link_base:
            return self._append_query(self.payment_link_base, {
                "user_id": user_id,
                "amount": f"{amt:.2f}",
                "type": "topup",
                "minutes": str(mins),
            })
        return f"https://payform.prodamus.ru/?user_id={user_id}&amount={amt:.2f}&type=topup&minutes={mins}"

    async def handle_webhook(self, payload: Dict) -> Dict:
        """
        Применяет оплату:
          • Идемпотентность по payment_id
          • type: pro/topup
          • minutes (для topup)
        """
        try:
            user_id = self._extract_user_id(payload)
            if not user_id:
                return {"success": False, "error": "No user_id in webhook payload"}

            event = (payload.get("event") or "").lower()
            status = (payload.get("status") or "").lower()

            # тип операции (в metadata/params/custom_fields/order)
            pay_type = ""
            for key in ("params", "custom_fields", "order", "metadata"):
                d = payload.get(key) or {}
                if isinstance(d, dict) and d.get("type"):
                    pay_type = str(d.get("type"))
            pay_type = (pay_type or "").lower()

            minutes = self._extract_minutes(payload)

            payment_id = self._extract_payment_id(payload) or ""
            if payment_id and storage.is_payment_processed("prodamus", payment_id):
                logger.info("Prodamus: duplicate webhook ignored (%s)", payment_id)
                return {"success": True, "message": "already processed"}

            # Расширенная эвристика «оплачен»
            paid = (
                status in {"success", "paid", "succeeded", "completed", "done", "ok"}
                or ("paid" in event or "succeed" in event or "complete" in event)
                or bool(payload.get("paid") or payload.get("is_paid"))
            )

            if paid:
                if payment_id:
                    storage.mark_payment_processed("prodamus", payment_id)

                if pay_type == "topup" and minutes > 0:
                    storage.add_overage_seconds(user_id, minutes * 60)
                    logger.info("Prodamus: user %s TOPUP +%sm (payment_id=%s)", user_id, minutes, payment_id)
                    return {"success": True, "message": f"user {user_id} topped up {minutes}m"}

                # по умолчанию — PRO
                storage.add_pro(user_id)
                logger.info("Prodamus: user %s upgraded to PRO (payment_id=%s)", user_id, payment_id)
                return {"success": True, "message": f"user {user_id} upgraded to PRO"}

            refunded = (status in {"refund", "refunded"}) or ("refund" in event)
            if refunded:
                logger.info("Prodamus: refund for user %s (payment_id=%s)", user_id, payment_id)
                return {"success": True, "message": "refund processed (no change)"}

            logger.info("Prodamus: webhook received (no change) event=%s status=%s payment_id=%s", event, status, payment_id)
            return {"success": True, "message": "Webhook received"}
        except Exception as e:
            logger.exception("Prodamus webhook error")
            return {"success": False, "error": str(e)}

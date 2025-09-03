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
    - handle_webhook: применяет PRO или докупку, с идемпотентностью по payment_id
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

    # ----------------- Вспомогательные -----------------

    def _extract_signature(self, headers: Dict[str, str]) -> Optional[str]:
        """Пытаемся достать подпись из разных заголовков, без учёта регистра."""
        if not headers:
            return None
        # прямое совпадение
        for key in self.SIGNATURE_HEADER_CANDIDATES:
            if key in headers:
                return headers.get(key)
        # без учёта регистра
        lower_map = {k.lower(): v for k, v in headers.items()}
        for key in self.SIGNATURE_HEADER_CANDIDATES:
            v = lower_map.get(key.lower())
            if v:
                return v
        return None

    @staticmethod
    def _normalize_signature(sig: str) -> str:
        """Удаляем возможный префикс 'sha256=' и приводим к нижнему регистру."""
        s = (sig or "").strip().strip('"').strip("'")
        if s.lower().startswith("sha256="):
            s = s[7:]
        return s.lower()

    def verify_webhook_signature(self, raw_payload: bytes, headers: Dict[str, str]) -> bool:
        """HMAC-SHA256 по всему «сыроому» телу запроса."""
        try:
            signature = self._extract_signature(headers)
            if not signature:
                logger.warning("Prodamus: подпись вебхука отсутствует")
                return False
            expected = hmac.new(self.webhook_secret, raw_payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, self._normalize_signature(signature))
        except Exception as e:
            logger.error(f"Prodamus: ошибка проверки подписи: {e}")
            return False

    def _extract_user_id(self, payload: Dict) -> Optional[int]:
        """
        Пытаемся достать user_id из разных мест.
        """
        candidates = [
            payload.get("user_id"),
            (payload.get("order") or {}).get("user_id"),
            (payload.get("client") or {}).get("user_id"),
            (payload.get("customer") or {}).get("user_id"),
            # часто user_id кладут в метаданные:
            ((payload.get("custom_fields") or {}).get("user_id") if isinstance(payload.get("custom_fields"), dict) else None),
            ((payload.get("params") or {}).get("user_id") if isinstance(payload.get("params"), dict) else None),
            # запасные варианты
            (payload.get("user") or {}).get("id") if isinstance(payload.get("user"), dict) else None,
            (payload.get("customer") or {}).get("id") if isinstance(payload.get("customer"), dict) else None,
        ]
        for v in candidates:
            if v is None:
                continue
            try:
                return int(v)
            except Exception:
                continue
        return None

    def _extract_minutes(self, payload: Dict) -> int:
        """Ищем minutes в разных местах."""
        paths = [
            ("params", "minutes"),
            ("custom_fields", "minutes"),
            ("order", "minutes"),
            ("metadata", "minutes"),
        ]
        for p, k in paths:
            d = payload.get(p) or {}
            if isinstance(d, dict) and k in d:
                try:
                    return int(d[k])
                except Exception:
                    pass
        return 0

    def _extract_payment_id(self, payload: Dict) -> Optional[str]:
        """
        Находим уникальный идентификатор платежа/счёта, чтобы обеспечить идемпотентность.
        """
        cands = [
            payload.get("id"),
            payload.get("payment_id"),
            payload.get("invoice_id"),
            payload.get("transaction_id"),
            payload.get("uuid"),
            payload.get("hash"),
            # вложенные
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

    # ----------------- Публичные методы -----------------

    # === PRO ===
    def get_payment_url(self, user_id: int, amount: Optional[float] = None) -> str:
        amt = float(amount if amount is not None else self.default_amount)
        if self.payment_link_base:
            return self._append_query(self.payment_link_base, {
                "user_id": user_id,
                "amount": f"{amt:.2f}",
                "type": "pro"
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
        Основная логика применения платежа:
        - проверяем идемпотентность по payment_id
        - читаем тип 'pro'/'topup' и минуты
        - выдаём PRO или начисляем минуты
        """
        try:
            user_id = self._extract_user_id(payload)
            if not user_id:
                return {"success": False, "error": "No user_id in webhook payload"}

            event = (payload.get("event") or "").lower()
            status = (payload.get("status") or "").lower()

            # тип операции (где чаще всего лежит)
            pay_type = ""
            for path in ("params", "custom_fields", "order", "metadata"):
                d = payload.get(path) or {}
                if isinstance(d, dict) and d.get("type"):
                    pay_type = d.get("type")
            pay_type = (pay_type or "").lower()

            minutes = self._extract_minutes(payload)

            # идемпотентность
            payment_id = self._extract_payment_id(payload) or ""
            if payment_id and storage.is_payment_processed("prodamus", payment_id):
                logger.info("Prodamus: duplicate webhook ignored (%s)", payment_id)
                return {"success": True, "message": "already processed"}

            # некоторые инсталляции присылают разные статусы
            paid = (
                status in ("success", "paid", "succeeded", "completed", "done", "ok")
                or ("paid" in event or "succeed" in event or "complete" in event)
            )

            if paid:
                # помечаем до побочных действий (защита от дублей)
                if payment_id:
                    storage.mark_payment_processed("prodamus", payment_id)

                if pay_type == "topup" and minutes > 0:
                    storage.add_overage_seconds(user_id, minutes * 60)
                    logger.info(f"Prodamus: user {user_id} TOPUP +{minutes}m (payment_id={payment_id})")
                    return {"success": True, "message": f"User {user_id} topped up {minutes}m"}

                # по умолчанию — апгрейд до PRO
                storage.add_pro(user_id)
                logger.info(f"Prodamus: user {user_id} upgraded to PRO (payment_id={payment_id})")
                return {"success": True, "message": f"User {user_id} upgraded to PRO"}

            refunded = (status in ("refund", "refunded")) or ("refund" in event)
            if refunded:
                # докупку мин обычно не откатываем
                logger.info(f"Prodamus: refund event for user {user_id} (payment_id={payment_id})")
                return {"success": True, "message": "refund processed (no change)"}

            logger.info(f"Prodamus: webhook received (no change): event={event}, status={status}, payment_id={payment_id}")
            return {"success": True, "message": "Webhook received"}
        except Exception as e:
            logger.error(f"Prodamus webhook error: {e}")
            return {"success": False, "error": str(e)}

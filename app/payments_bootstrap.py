# app/payments_bootstrap.py
import logging
from app.config import (
    YOOKASSA_SHOP_ID,
    YOOKASSA_SECRET_KEY,
    YOOKASSA_RETURN_URL,
    YOOKASSA_PRO_AMOUNT,
    PRODAMUS_WEBHOOK_SECRET,
    PRODAMUS_PAYMENT_LINK,
    PRODAMUS_PRO_AMOUNT,
)

logger = logging.getLogger(__name__)

payment_manager = None  # будет инициализирован ниже

def _mask(v: str | None, keep: int = 4) -> str:
    if not v:
        return ""
    v = str(v)
    if len(v) <= keep:
        return "*" * len(v)
    return "*" * (len(v) - keep) + v[-keep:]

def _enabled_yk() -> bool:
    return bool((YOOKASSA_SHOP_ID or "").strip() and (YOOKASSA_SECRET_KEY or "").strip())

def _enabled_prodamus() -> bool:
    return bool((PRODAMUS_WEBHOOK_SECRET or "").strip())

try:
    if _enabled_yk():
        # Инициализируем YooKassa
        try:
            from app.yookassa_manager import YooKassaManager
        except Exception as e:
            logger.error("Не удалось импортировать YooKassaManager: %s", e, exc_info=True)
            raise

        payment_manager = YooKassaManager(
            shop_id=str(YOOKASSA_SHOP_ID).strip(),
            secret_key=str(YOOKASSA_SECRET_KEY).strip(),
            return_url=(YOOKASSA_RETURN_URL or "https://t.me").strip(),
            default_amount=float(YOOKASSA_PRO_AMOUNT or 0.0),
        )
        logger.info(
            "Payments: YooKassa enabled (shop_id=%s, key=%s, return_url=%s, amount=%.2f)",
            _mask(YOOKASSA_SHOP_ID), _mask(YOOKASSA_SECRET_KEY), (YOOKASSA_RETURN_URL or "—"), float(YOOKASSA_PRO_AMOUNT or 0.0)
        )

    elif _enabled_prodamus():
        # Инициализируем Prodamus
        try:
            from app.payment_manager import PaymentManager  # твой Prodamus-менеджер
        except Exception as e:
            logger.error("Не удалось импортировать PaymentManager (Prodamus): %s", e, exc_info=True)
            raise

        payment_manager = PaymentManager(
            webhook_secret=str(PRODAMUS_WEBHOOK_SECRET).strip(),
            payment_link_base=(PRODAMUS_PAYMENT_LINK or None),
            default_amount=float(PRODAMUS_PRO_AMOUNT or 0.0),
        )
        logger.info(
            "Payments: Prodamus enabled (secret=%s, base_link=%s, amount=%.2f)",
            _mask(PRODAMUS_WEBHOOK_SECRET), (PRODAMUS_PAYMENT_LINK or "—"), float(PRODAMUS_PRO_AMOUNT or 0.0)
        )
    else:
        logger.warning("Payments: disabled (no provider configured)")

except Exception:
    # Если что-то пошло не так на этапе инициализации — не валим импорт, а просто отключаем оплаты
    logger.exception("Отключаю платежи из-за ошибки инициализации")
    payment_manager = None

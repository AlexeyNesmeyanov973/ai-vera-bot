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
from app.yookassa_manager import YooKassaManager
from app.payment_manager import PaymentManager  # Prodamus

logger = logging.getLogger(__name__)

# Выбор платёжного провайдера:
# 1) Если заданы ключи YooKassa — используем её.
# 2) Иначе, если задан Prodamus — используем Prodamus.
# 3) Иначе оплаты отключены.
payment_manager = None

if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    payment_manager = YooKassaManager(
        shop_id=YOOKASSA_SHOP_ID,
        secret_key=YOOKASSA_SECRET_KEY,
        return_url=YOOKASSA_RETURN_URL or "https://t.me",
        default_amount=YOOKASSA_PRO_AMOUNT,
    )
    logger.info("Payments: YooKassa enabled")
elif PRODAMUS_WEBHOOK_SECRET:
    payment_manager = PaymentManager(
        webhook_secret=PRODAMUS_WEBHOOK_SECRET,
        payment_link_base=PRODAMUS_PAYMENT_LINK or None,
        default_amount=PRODAMUS_PRO_AMOUNT,
    )
    logger.info("Payments: Prodamus enabled")
else:
    logger.warning("Payments: disabled (no provider configured)")

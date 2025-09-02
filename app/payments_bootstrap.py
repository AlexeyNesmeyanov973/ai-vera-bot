from app.config import (
    PRODAMUS_WEBHOOK_SECRET,
    PRODAMUS_PAYMENT_LINK,
    PRODAMUS_PRO_AMOUNT,
)
from app.payment_manager import PaymentManager

payment_manager = None
if PRODAMUS_WEBHOOK_SECRET:
    payment_manager = PaymentManager(
        webhook_secret=PRODAMUS_WEBHOOK_SECRET,
        payment_link_base=PRODAMUS_PAYMENT_LINK or None,
        default_amount=PRODAMUS_PRO_AMOUNT,
    )

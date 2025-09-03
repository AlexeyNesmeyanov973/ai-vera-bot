# app/web.py
import logging
import asyncio
from flask import Flask, request, jsonify, Response
from prometheus_client import Counter, Summary, generate_latest, CONTENT_TYPE_LATEST

# Логгер
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Flask app
app = Flask(__name__)

# --- Prometheus metrics ---
# Латентность (без лейблов — можно использовать как декоратор .time())
WEBHOOK_LATENCY = Summary("webhook_latency_seconds", "Webhook handler latency")
# Ошибки с причиной (лейбл reason обязателен при инкременте)
WEBHOOK_ERRORS_TOTAL = Counter("webhook_errors_total", "Webhook errors total", ["reason"])

# Healthcheck (Render healthCheckPath)
@app.get("/health")
def health():
    return jsonify({"ok": True})

# Прометеус-эндпоинт
@app.get("/metrics")
def metrics():
    data = generate_latest()
    return Response(data, mimetype=CONTENT_TYPE_LATEST)

# --------- Prodamus webhook ----------
@app.post("/webhook/prodamus")
@WEBHOOK_LATENCY.time()
def webhook_prodamus():
    # ленивый импорт менеджера платежей, чтобы не тянуть его в worker-процессы
    from app.payments_bootstrap import payment_manager
    if not payment_manager:
        WEBHOOK_ERRORS_TOTAL.labels(reason="payments_disabled").inc()
        return jsonify({"error": "Payments disabled"}), 503

    try:
        # raw нужен для проверки подписи
        raw = request.get_data()
        headers = dict(request.headers)

        # Превью (для логов и вытаскивания id)
        payload = request.get_json(silent=True) or {}

        # Попробуем извлечь payment_id для логов (не критично)
        pid = None
        try:
            if hasattr(payment_manager, "_extract_payment_id"):
                pid = payment_manager._extract_payment_id(payload)  # приватный, но безопасно
        except Exception:
            pid = None

        # Проверка подписи (если реализована)
        try:
            if hasattr(payment_manager, "verify_webhook_signature"):
                ok = payment_manager.verify_webhook_signature(raw, headers)
                if not ok:
                    logger.warning(f"Prodamus webhook: invalid signature (pid={pid})")
                    WEBHOOK_ERRORS_TOTAL.labels(reason="bad_signature").inc()
                    return jsonify({"error": "Invalid signature", "payment_id": pid}), 401
        except Exception:
            # на всякий случай не роняем обработчик
            logger.exception("Signature verification error (prodamus)")
            WEBHOOK_ERRORS_TOTAL.labels(reason="sig_verify_exception").inc()
            return jsonify({"error": "Signature verification failed"}), 400

        # Вызов асинхронного обработчика
        result = asyncio.run(payment_manager.handle_webhook(payload))

        if result.get("success"):
            logger.info(f"Prodamus webhook OK (pid={pid}): {result.get('message')}")
            out = dict(result)
            if pid:
                out["payment_id"] = pid
            return jsonify(out), 200

        logger.warning(f"Prodamus webhook handled with error (pid={pid}): {result}")
        WEBHOOK_ERRORS_TOTAL.labels(reason="handler_error").inc()
        out = dict(result)
        if pid:
            out["payment_id"] = pid
        return jsonify(out), 400

    except Exception:
        logger.exception("Webhook error (prodamus)")
        WEBHOOK_ERRORS_TOTAL.labels(reason="exception").inc()
        return jsonify({"error": "Internal error"}), 500

# --------- YooKassa webhook ----------
@app.post("/webhook/yookassa")
@WEBHOOK_LATENCY.time()
def webhook_yookassa():
    from app.payments_bootstrap import payment_manager
    if not payment_manager:
        WEBHOOK_ERRORS_TOTAL.labels(reason="payments_disabled").inc()
        return jsonify({"error": "Payments disabled"}), 503

    try:
        payload = request.get_json(silent=True) or {}

        # Для логов достанем id из object.id, если есть
        pid = None
        try:
            obj = payload.get("object") or {}
            pid = obj.get("id") or obj.get("payment_id")
        except Exception:
            pid = None

        result = asyncio.run(payment_manager.handle_webhook(payload))

        if result.get("success"):
            logger.info(f"YooKassa webhook OK (pid={pid}): {result.get('message')}")
            out = dict(result)
            if pid:
                out["payment_id"] = pid
            return jsonify(out), 200

        logger.warning(f"YooKassa webhook handled with error (pid={pid}): {result}")
        WEBHOOK_ERRORS_TOTAL.labels(reason="handler_error").inc()
        out = dict(result)
        if pid:
            out["payment_id"] = pid
        return jsonify(out), 400

    except Exception:
        logger.exception("Webhook error (yookassa)")
        WEBHOOK_ERRORS_TOTAL.labels(reason="exception").inc()
        return jsonify({"error": "Internal error"}), 500

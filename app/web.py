import logging
from app.config import LOG_LEVEL
import asyncio
from flask import Flask, request, jsonify, Response
from prometheus_client import Counter, Summary, generate_latest, CONTENT_TYPE_LATEST

logger = logging.getLogger(__name__)
try:
    logging.basicConfig(level=getattr(logging, str(LOG_LEVEL).upper(), logging.INFO))
except Exception:
    logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# --- Prometheus metrics ---
WEBHOOK_LATENCY = Summary("webhook_latency_seconds", "Webhook handler latency")
WEBHOOK_ERRORS_TOTAL = Counter("webhook_errors_total", "Webhook errors total", ["reason"])

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.get("/metrics")
def metrics():
    data = generate_latest()
    return Response(data, mimetype=CONTENT_TYPE_LATEST)

# --- small helper to run async safely from WSGI context ---
def _run_async(coro):
    """
    Надёжный запуск корутины из Flask (WSGI) обработчика.
    Работает корректно под обычным gunicorn (рекомендовано).
    """
    try:
        # Если цикл в этом потоке не запущен — обычный путь
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Теоретический кейс, когда мы реально внутри уже запущенного цикла:
    # создадим новый loop в отдельном thread-safe вызове.
    loop = asyncio.get_event_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result()

# --------- Prodamus webhook ----------
@app.post("/webhook/prodamus")
@WEBHOOK_LATENCY.time()
def webhook_prodamus():
    from app.payments_bootstrap import payment_manager
    if not payment_manager:
        WEBHOOK_ERRORS_TOTAL.labels(reason="payments_disabled").inc()
        return jsonify({"error": "Payments disabled"}), 503

    try:
        raw = request.get_data()            # байтовый payload для подписи
        headers = dict(request.headers)     # заголовки (подписанные)
        payload = request.get_json(silent=True) or {}

        # Попробуем достать payment_id для логов (не критично)
        pid = None
        try:
            if hasattr(payment_manager, "_extract_payment_id"):
                pid = payment_manager._extract_payment_id(payload)
        except Exception:
            pid = None

        # Проверка подписи (если менеджер умеет)
        try:
            if hasattr(payment_manager, "verify_webhook_signature"):
                ok = payment_manager.verify_webhook_signature(raw, headers)
                if not ok:
                    logger.warning("Prodamus webhook: invalid signature (pid=%s)", pid)
                    WEBHOOK_ERRORS_TOTAL.labels(reason="bad_signature").inc()
                    return jsonify({"error": "Invalid signature", "payment_id": pid}), 401
        except Exception:
            logger.exception("Signature verification error (prodamus)")
            WEBHOOK_ERRORS_TOTAL.labels(reason="sig_verify_exception").inc()
            return jsonify({"error": "Signature verification failed"}), 400

        # Запускаем асинхронный обработчик
        result = _run_async(payment_manager.handle_webhook(payload))

        if result.get("success"):
            logger.info("Prodamus webhook OK (pid=%s): %s", pid, result.get("message"))
            out = dict(result)
            if pid:
                out["payment_id"] = pid
            return jsonify(out), 200

        logger.warning("Prodamus webhook handled with error (pid=%s): %s", pid, result)
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

        result = _run_async(payment_manager.handle_webhook(payload))

        if result.get("success"):
            logger.info("YooKassa webhook OK (pid=%s): %s", pid, result.get("message"))
            out = dict(result)
            if pid:
                out["payment_id"] = pid
            return jsonify(out), 200

        logger.warning("YooKassa webhook handled with error (pid=%s): %s", pid, result)
        WEBHOOK_ERRORS_TOTAL.labels(reason="handler_error").inc()
        out = dict(result)
        if pid:
            out["payment_id"] = pid
        return jsonify(out), 400

    except Exception:
        logger.exception("Webhook error (yookassa)")
        WEBHOOK_ERRORS_TOTAL.labels(reason="exception").inc()
        return jsonify({"error": "Internal error"}), 500

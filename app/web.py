import os
import logging
from flask import Flask, request, jsonify, Response
from app.bootstrap import run_startup_migrations

# Prometheus
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUESTS_TOTAL = Counter("web_requests_total", "Total web requests", ["endpoint", "method"])
WEBHOOK_ERRORS_TOTAL = Counter("webhook_errors_total", "Webhook errors", ["reason"])
WEBHOOK_LATENCY = Histogram("webhook_latency_seconds", "Webhook processing time")

app = Flask(__name__)

@app.before_first_request
def _migrate_on_first_request():
    try:
        run_startup_migrations()
    except Exception:
        logger.exception("Startup migrations failed in web app")

@app.before_request
def _before_request():
    try:
        REQUESTS_TOTAL.labels(endpoint=request.path, method=request.method).inc()
    except Exception:
        pass

@app.route("/metrics", methods=["GET"])
def metrics():
    data = generate_latest()
    return Response(data, mimetype=CONTENT_TYPE_LATEST)

@app.route("/webhook/prodamus", methods=["POST"])
@WEBHOOK_LATENCY.time()
def webhook_prodamus():
    from app.payments_bootstrap import payment_manager
    if not payment_manager:
        WEBHOOK_ERRORS_TOTAL.labels(reason="payments_disabled").inc()
        return jsonify({"error": "Payments disabled"}), 503
    try:
        raw = request.get_data()
        headers = dict(request.headers)
        # Prodamus требует верификацию подписи
        if hasattr(payment_manager, "verify_webhook_signature"):
            if not payment_manager.verify_webhook_signature(raw, headers):
                WEBHOOK_ERRORS_TOTAL.labels(reason="bad_signature").inc()
                return jsonify({"error": "Invalid signature"}), 401

        data = request.get_json(silent=True) or {}
        import asyncio
        result = asyncio.run(payment_manager.handle_webhook(data))
        if result.get("success"):
            return jsonify(result), 200
        else:
            WEBHOOK_ERRORS_TOTAL.labels(reason="handler_error").inc()
            return jsonify(result), 400
    except Exception:
        logger.exception("Webhook error (prodamus)")
        WEBHOOK_ERRORS_TOTAL.labels(reason="exception").inc()
        return jsonify({"error": "Internal error"}), 500

@app.route("/webhook/yookassa", methods=["POST"])
@WEBHOOK_LATENCY.time()
def webhook_yookassa():
    from app.payments_bootstrap import payment_manager
    if not payment_manager:
        WEBHOOK_ERRORS_TOTAL.labels(reason="payments_disabled").inc()
        return jsonify({"error": "Payments disabled"}), 503
    try:
        # Для YooKassa не нужна подпись — перепроверяем по API в менеджере
        data = request.get_json(silent=True) or {}
        import asyncio
        result = asyncio.run(payment_manager.handle_webhook(data))
        if result.get("success"):
            return jsonify(result), 200
        else:
            WEBHOOK_ERRORS_TOTAL.labels(reason="handler_error").inc()
            return jsonify(result), 400
    except Exception:
        logger.exception("Webhook error (yookassa)")
        WEBHOOK_ERRORS_TOTAL.labels(reason="exception").inc()
        return jsonify({"error": "Internal error"}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    run_startup_migrations()
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

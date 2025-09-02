import os
import logging
from flask import Flask, request, jsonify, Response
from app.config import payment_manager
from app.bootstrap import run_startup_migrations

# Prometheus
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Метрики
REQUESTS_TOTAL = Counter("web_requests_total", "Total web requests", ["endpoint", "method"])
WEBHOOK_ERRORS_TOTAL = Counter("webhook_errors_total", "Webhook errors", ["reason"])
WEBHOOK_LATENCY = Histogram("webhook_latency_seconds", "Webhook processing time")

app = Flask(__name__)

@app.before_request
def _before_request():
    try:
        REQUESTS_TOTAL.labels(endpoint=request.path, method=request.method).inc()
    except Exception:
        pass

@app.route("/metrics", methods=["GET"])
def metrics():
    """Экспорт метрик Prometheus."""
    data = generate_latest()
    return Response(data, mimetype=CONTENT_TYPE_LATEST)

@app.route("/webhook/paydmus", methods=["POST"])
@WEBHOOK_LATENCY.time()
def webhook_paydmus():
    if not payment_manager:
        WEBHOOK_ERRORS_TOTAL.labels(reason="payments_disabled").inc()
        return jsonify({"error": "Payments disabled"}), 503
    try:
        signature = request.headers.get('X-Paydmus-Signature')
        payload = request.get_data()

        if not signature:
            WEBHOOK_ERRORS_TOTAL.labels(reason="no_signature").inc()
            return jsonify({"error": "Missing signature"}), 401

        if not payment_manager.verify_webhook_signature(payload, signature):
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
    except Exception as e:
        logger.exception("Webhook error")
        WEBHOOK_ERRORS_TOTAL.labels(reason="exception").inc()
        return jsonify({"error": "Internal error"}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    # Миграция PRO из ENV → Redis/Postgres (идемпотентно)
    run_startup_migrations()

    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

import os
import logging
from flask import Flask, request, jsonify
from app.config import payment_manager
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route("/webhook/paydmus", methods=["POST"])
def webhook_paydmus():
    if not payment_manager:
        return jsonify({"error": "Payments disabled"}), 503
    try:
        signature = request.headers.get('X-Paydmus-Signature')
        payload = request.get_data()

        if not signature or not payment_manager.verify_webhook_signature(payload, signature):
            return jsonify({"error": "Invalid signature"}), 401

        data = request.get_json(silent=True) or {}
        result = asyncio.run(payment_manager.handle_webhook(data))
        return (jsonify(result), 200) if result.get("success") else (jsonify(result), 400)
    except Exception as e:
        logger.exception("Webhook error")
        return jsonify({"error": "Internal error"}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

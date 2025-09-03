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

        # payment_id для логов/диагностики
        payload_preview = request.get_json(silent=True) or {}
        try:
            pid = None
            if hasattr(payment_manager, "_extract_payment_id"):
                pid = payment_manager._extract_payment_id(payload_preview)  # да, приватный, но безопасно
        except Exception:
            pid = None

        # Prodamus требует верификацию подписи
        if hasattr(payment_manager, "verify_webhook_signature"):
            ok = payment_manager.verify_webhook_signature(raw, headers)
            if not ok:
                logger.warning(f"Prodamus webhook: invalid signature (pid={pid})")
                WEBHOOK_ERRORS_TOTAL.labels(reason="bad_signature").inc()
                return jsonify({"error": "Invalid signature", "payment_id": pid}), 401

        data = payload_preview
        import asyncio
        result = asyncio.run(payment_manager.handle_webhook(data))

        # логируем с payment_id
        if result.get("success"):
            logger.info(f"Prodamus webhook OK (pid={pid}): {result.get('message')}")
            out = dict(result)
            if pid:
                out["payment_id"] = pid
            return jsonify(out), 200
        else:
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


@app.route("/webhook/yookassa", methods=["POST"])
@WEBHOOK_LATENCY.time()
def webhook_yookassa():
    from app.payments_bootstrap import payment_manager
    if not payment_manager:
        WEBHOOK_ERRORS_TOTAL.labels(reason="payments_disabled").inc()
        return jsonify({"error": "Payments disabled"}), 503
    try:
        data = request.get_json(silent=True) or {}
        # вытащим id из object.id, если есть — пригодится в логах
        pid = None
        try:
            obj = data.get("object") or {}
            pid = obj.get("id") or obj.get("payment_id")
        except Exception:
            pid = None

        import asyncio
        result = asyncio.run(payment_manager.handle_webhook(data))
        if result.get("success"):
            logger.info(f"YooKassa webhook OK (pid={pid}): {result.get('message')}")
            out = dict(result)
            if pid:
                out["payment_id"] = pid
            return jsonify(out), 200
        else:
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

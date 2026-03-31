"""
POST /receipt/print  — Manually trigger a receipt print.

Optional query parameters
--------------------------
?time=morning   Force the morning greeting ("Good Morning")
?time=evening   Force the evening greeting ("Good Evening")
(omit)          Auto-detect from current NY time (hour < 12 → morning)
"""

import logging
import os

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

receipt_bp = Blueprint("receipt", __name__)


@receipt_bp.route("/receipt/print", methods=["POST"])
def manual_print():
    if not os.environ.get("RECEIPT_PRINTER_IP"):
        return jsonify({"success": False, "error": "RECEIPT_PRINTER_IP not configured"}), 500

    time_param = request.args.get("time", "").lower()
    if time_param == "morning":
        is_morning: bool | None = True
    elif time_param == "evening":
        is_morning = False
    else:
        is_morning = None  # auto-detect inside run_print

    try:
        from ..receipt_printer import run_print
        run_print(is_morning=is_morning)  # already inside a request context
        return jsonify({"success": True})
    except Exception as exc:
        logger.exception("Manual receipt print failed")
        return jsonify({"success": False, "error": str(exc)}), 500

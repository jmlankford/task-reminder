"""
POST /gcal/sync  — Manually trigger a Google Calendar sync.

Optionally protected by GCAL_API_KEY (X-API-Key header or ?api_key= param).
If GCAL_API_KEY is not set the endpoint is open (still only POST).
"""

import logging
import os

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

gcal_bp = Blueprint("gcal", __name__)


def _authorised() -> bool:
    expected = os.environ.get("GCAL_API_KEY", "")
    if not expected:
        return True  # key not configured → open endpoint
    provided = request.headers.get("X-API-Key") or request.args.get("api_key", "")
    return provided == expected


@gcal_bp.route("/gcal/sync", methods=["POST"])
def manual_sync():
    if not _authorised():
        return jsonify({"success": False, "error": "Unauthorised"}), 401

    try:
        from ..gcal_sync import run_sync
        summary = run_sync()  # already inside a request context
        return jsonify({"success": True, **summary})
    except FileNotFoundError as exc:
        logger.error("GCal sync failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500
    except Exception as exc:
        logger.exception("GCal sync failed")
        return jsonify({"success": False, "error": str(exc)}), 500

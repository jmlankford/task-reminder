from flask import Blueprint, request, jsonify
from ..models import db, Config

config_bp = Blueprint("config", __name__)

ALLOWED_KEYS = {"max_active"}


def ok(data=None, message=None, status_code=200):
    resp = {"success": True}
    if data is not None:
        resp["data"] = data
    if message:
        resp["message"] = message
    return jsonify(resp), status_code


def err(message, status_code=400):
    return jsonify({"success": False, "error": message}), status_code


def _config_snapshot():
    return {c.key: c.value for c in Config.query.all()}


# ── GET /config ───────────────────────────────────────────────────────────────

@config_bp.route("/config", methods=["GET"])
def get_config():
    """Return all current configuration values."""
    return ok(_config_snapshot())


# ── PUT /config ───────────────────────────────────────────────────────────────

@config_bp.route("/config", methods=["PUT"])
def update_config():
    """
    Update one or more config values.
    Currently supported keys:
      - max_active (int >= 1): maximum number of active+snoozed reminders allowed.
    """
    data = request.get_json(silent=True)
    if not data:
        return err("Request body must be JSON.")

    unknown = set(data.keys()) - ALLOWED_KEYS
    if unknown:
        return err(f'Unknown config key(s): {", ".join(sorted(unknown))}. Allowed: {", ".join(sorted(ALLOWED_KEYS))}.')

    if "max_active" in data:
        try:
            val = int(data["max_active"])
        except (ValueError, TypeError):
            return err("max_active must be an integer.")
        if val < 1:
            return err("max_active must be at least 1.")

        config = Config.query.get("max_active")
        if config:
            config.value = str(val)
        else:
            db.session.add(Config(key="max_active", value=str(val)))
        db.session.commit()

    return ok(_config_snapshot(), message="Config updated.")

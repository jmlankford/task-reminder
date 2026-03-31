"""
Home Assistant integration routes — Part 7.

Inbound  (HA → Flask)
─────────────────────
  POST /ha/trigger-check
      Called by the HA evening presence automation. Sends a Telegram summary
      of all active reminders.  Auth: X-API-Key header (HA_API_KEY).

Outbound helpers (Flask → HA webhooks)
───────────────────────────────────────
  POST /ha/light-on
      Tells HA to start the amber pulse on the studio light bar.

  POST /ha/light-off
      Tells HA to stop the pulse and turn the light off.

  GET|POST /ha/announce?message=…   or   body: {"message": "…"}
      Tells HA to announce a message through the bath Echo.

Environment variables
─────────────────────
  HA_API_KEY              Shared secret — HA sends this; Flask verifies it.
                          Must match `task_reminder_ha_api_key` in secrets.yaml.
  HA_WEBHOOK_BASE_URL     e.g. http://192.168.1.165:8123
  HA_LIGHT_ON_WEBHOOK_ID  Webhook ID in HA for the light-on automation
                          (default: task-reminder-light-on)
  HA_LIGHT_OFF_WEBHOOK_ID Webhook ID for light-off (default: task-reminder-light-off)
  HA_ANNOUNCE_WEBHOOK_ID  Webhook ID for announce  (default: task-reminder-announce)
"""

import logging
import os

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
ha_bp = Blueprint("ha", __name__)


# ── Auth ───────────────────────────────────────────────────────────────────────

def _authorised() -> bool:
    expected = os.environ.get("HA_API_KEY", "")
    if not expected:
        return True   # key not configured → open (not recommended for production)
    provided = request.headers.get("X-API-Key") or request.args.get("api_key", "")
    return provided == expected


# ── HA webhook helper ──────────────────────────────────────────────────────────

def _ha_post(webhook_id_env: str, default_id: str, params: dict | None = None) -> tuple[bool, str]:
    """
    POST to an HA webhook endpoint.
    Returns (success, error_string).
    """
    base = os.environ.get("HA_WEBHOOK_BASE_URL", "").rstrip("/")
    if not base:
        return False, "HA_WEBHOOK_BASE_URL not configured"

    webhook_id = os.environ.get(webhook_id_env, default_id)
    url = f"{base}/api/webhook/{webhook_id}"

    try:
        r = requests.post(url, params=params, timeout=10)
        # HA returns 200 or 201 for webhook hits — anything else is unexpected
        r.raise_for_status()
        return True, ""
    except requests.RequestException as exc:
        logger.error("HA webhook %s failed: %s", webhook_id, exc)
        return False, str(exc)


# ── Inbound: HA → Flask ────────────────────────────────────────────────────────

@ha_bp.route("/ha/trigger-check", methods=["POST"])
def trigger_check():
    """
    Evening presence check called by Home Assistant.
    Sends a Telegram message listing all active reminders.
    """
    if not _authorised():
        return jsonify({"success": False, "error": "Unauthorised"}), 401

    from ..models import Reminder

    reminders = (
        Reminder.query
        .filter(Reminder.status == "active", Reminder.deleted_at.is_(None))
        .order_by(Reminder.priority.desc(), Reminder.created_at.asc())
        .all()
    )

    if not reminders:
        logger.info("HA trigger-check: no active reminders.")
        return jsonify({"success": True, "message": "No active reminders.", "count": 0})

    chat_id_str = os.environ.get("TELEGRAM_CHAT_ID", "")
    if chat_id_str:
        lines = ["🏠 *Evening Check-In*\n"]
        for i, r in enumerate(reminders, 1):
            overdue_flag = " ⚠️" if r.overdue else ""
            lines.append(f"{i}. *{r.title}* [P{r.priority}]{overdue_flag}")
        lines.append("\n_Reply with a number + `done` or `snooze`_")

        try:
            from ..telegram_bot import send_message_sync, set_last_list
            send_message_sync(int(chat_id_str), "\n".join(lines))
            set_last_list(int(chat_id_str), [r.id for r in reminders])
        except Exception:
            logger.exception("Failed to send HA check-in Telegram message")

    logger.info("HA trigger-check: notified %d reminder(s).", len(reminders))
    return jsonify({"success": True, "count": len(reminders)})


# ── Outbound: Flask → HA ───────────────────────────────────────────────────────

@ha_bp.route("/ha/light-on", methods=["POST"])
def light_on():
    """Trigger the HA amber pulse automation."""
    ok, err = _ha_post("HA_LIGHT_ON_WEBHOOK_ID", "task-reminder-light-on")
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": err}), 500


@ha_bp.route("/ha/light-off", methods=["POST"])
def light_off():
    """Trigger the HA light-off automation."""
    ok, err = _ha_post("HA_LIGHT_OFF_WEBHOOK_ID", "task-reminder-light-off")
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": err}), 500


@ha_bp.route("/ha/announce", methods=["GET", "POST"])
def announce():
    """
    Proxy an announce request to HA's Alexa webhook.
    Accepts the message via query string (?message=…) or JSON body.
    """
    message = (
        request.args.get("message")
        or (request.get_json(silent=True) or {}).get("message", "")
    )
    if not message:
        return jsonify({"success": False, "error": "message is required"}), 400

    ok, err = _ha_post(
        "HA_ANNOUNCE_WEBHOOK_ID",
        "task-reminder-announce",
        params={"message": message},
    )
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": err}), 500

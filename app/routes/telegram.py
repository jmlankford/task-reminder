"""
POST /telegram/notify  — Home Assistant webhook endpoint.

Home Assistant calls this endpoint (with the API key) to trigger proactive
Telegram notifications for all active reminders that haven't been messaged
within the configured cooldown window.
"""

import logging
import os
from datetime import timedelta

from flask import Blueprint, jsonify, request

from ..models import db, Reminder
from ..utils import now_utc

logger = logging.getLogger(__name__)

telegram_bp = Blueprint("telegram", __name__)


def _authorised() -> bool:
    """Check X-API-Key header or ?api_key= query param against TELEGRAM_NOTIFY_API_KEY."""
    expected = os.environ.get("TELEGRAM_NOTIFY_API_KEY", "")
    if not expected:
        return False  # key not configured → always reject
    provided = request.headers.get("X-API-Key") or request.args.get("api_key", "")
    return provided == expected


@telegram_bp.route("/telegram/notify", methods=["POST"])
def notify():
    if not _authorised():
        return jsonify({"success": False, "error": "Unauthorised"}), 401

    chat_id_str = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not chat_id_str:
        return jsonify({"success": False, "error": "TELEGRAM_CHAT_ID not configured"}), 500

    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return jsonify({"success": False, "error": "TELEGRAM_CHAT_ID must be an integer"}), 500

    cooldown_hours = float(os.environ.get("TELEGRAM_NOTIFY_COOLDOWN_HOURS", 4))
    now = now_utc()
    cutoff = now - timedelta(hours=cooldown_hours)

    # Active reminders not notified within the cooldown window
    qualifying = (
        Reminder.query.filter(
            Reminder.status == "active",
            Reminder.deleted_at.is_(None),
            db.or_(
                Reminder.last_notified_at.is_(None),
                Reminder.last_notified_at <= cutoff,
            ),
        )
        .order_by(
            Reminder.overdue.desc(),
            Reminder.priority.desc(),
            Reminder.created_at.asc(),
        )
        .all()
    )

    if not qualifying:
        return jsonify({"success": True, "message": "No reminders due for notification", "sent": 0})

    from ..telegram_bot import send_message_sync, set_last_list, _fmt_date, _fmt_dt

    # Build the notification message with a numbered list
    lines = ["🔔 *Reminder Check-in*\n"]
    ids: list[int] = []

    for i, r in enumerate(qualifying, 1):
        overdue_flag = " ⚠️ OVERDUE" if r.overdue else ""
        due_str = f" — Due: {_fmt_date(r.due_date)}" if r.due_date else ""
        lines.append(f"{i}. *{r.title}* [P{r.priority}]{overdue_flag}{due_str}")
        ids.append(r.id)

    lines.append("\n_Reply `1 done`, `1 snooze`, or `1 snooze 4.5`_")
    message = "\n".join(lines)

    send_message_sync(chat_id, message)
    set_last_list(chat_id, ids)

    # Stamp each reminder so the cooldown window resets
    for r in qualifying:
        r.last_notified_at = now
    db.session.commit()

    titles = [r.title for r in qualifying]
    logger.info("Notified %d reminder(s) via Telegram: %s", len(titles), titles)

    return jsonify({"success": True, "sent": len(qualifying), "reminders": titles})

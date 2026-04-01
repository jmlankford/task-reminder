"""
Alexa Custom Skill webhook — Part 8.

Alexa POSTs JSON here for every skill interaction (launch, intents, session end).
The endpoint must be reachable over public HTTPS — use Cloudflare Tunnel or
similar reverse proxy (see alexa/setup_guide.md for full instructions).

Supported intents
-----------------
  AddTaskIntent      — add a task (title + due_date required)
  SnoozeTaskIntent   — snooze by title/number for duration or until date/time
  RemindAtIntent     — set remind_at on a task
  DeleteTaskIntent   — soft-delete by title/number
  MarkDoneIntent     — mark task done by title/number
  ListTasksIntent    — read off active tasks
  AMAZON.HelpIntent  — usage help
  AMAZON.CancelIntent / AMAZON.StopIntent — exit

Environment variables
---------------------
  ALEXA_SKILL_ID   Your skill's application ID (amzn1.ask.skill.…).
                   Set this to reject requests from unknown skills.
"""

import logging
import os
import urllib.request
import json as _json
from datetime import datetime

import pytz
from flask import Blueprint, jsonify, request

from ..models import Reminder, db
from .. import alexa_helper as ah

logger = logging.getLogger(__name__)
alexa_bp = Blueprint("alexa", __name__)

NY_TZ = pytz.timezone("America/New_York")
SOURCE = "alexa"


# ── Webhook entry point ───────────────────────────────────────────────────────

@alexa_bp.route("/alexa/webhook", methods=["POST"])
def webhook():
    body = request.get_json(force=True) or {}

    # Verify skill application ID
    skill_id = os.environ.get("ALEXA_SKILL_ID")
    if skill_id:
        app_id = (
            body.get("session", {})
                .get("application", {})
                .get("applicationId", "")
        )
        if app_id != skill_id:
            logger.warning("Rejected Alexa request — unknown skill ID: %s", app_id)
            return jsonify({"error": "Unauthorized"}), 403

    # Reject stale requests (Alexa requires responses within 150 seconds)
    ts_str = body.get("request", {}).get("timestamp", "")
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if abs((datetime.now(pytz.utc) - ts).total_seconds()) > 150:
                return jsonify({"error": "Request expired"}), 400
        except Exception:
            pass

    req = body.get("request", {})
    req_type = req.get("type", "")

    if req_type == "LaunchRequest":
        return jsonify(ah.respond(
            "Task Reminder is ready. "
            "You can add tasks, list them, snooze, mark them done, "
            "delete them, or set a reminder. What would you like to do?",
            reprompt="What would you like to do?",
            end_session=False,
        ))

    if req_type == "IntentRequest":
        intent = req.get("intent", {})
        return jsonify(_dispatch(intent.get("name", ""), intent.get("slots", {})))

    if req_type == "SessionEndedRequest":
        return jsonify(ah.respond("Goodbye!"))

    return jsonify(ah.respond("I'm not sure how to handle that. Goodbye."))


# ── Intent dispatcher ─────────────────────────────────────────────────────────

def _dispatch(name: str, slots: dict) -> dict:
    handlers = {
        "AddTaskIntent":       _add_task,
        "SnoozeTaskIntent":    _snooze_task,
        "RemindAtIntent":      _set_remind_at,
        "DeleteTaskIntent":    _delete_task,
        "MarkDoneIntent":      _mark_done,
        "ListTasksIntent":     _list_tasks,
        "AMAZON.HelpIntent":   _help,
        "AMAZON.CancelIntent": lambda s: ah.respond("Cancelled."),
        "AMAZON.StopIntent":   lambda s: ah.respond("Goodbye!"),
    }
    handler = handlers.get(name)
    if handler:
        return handler(slots)
    logger.warning("Unhandled Alexa intent: %s", name)
    return ah.respond("I'm not sure how to handle that request.")


# ── Intent handlers ───────────────────────────────────────────────────────────

def _add_task(slots: dict) -> dict:
    title      = ah.get_slot(slots, "title")
    due_str    = ah.get_slot(slots, "due_date")
    start_str  = ah.get_slot(slots, "start_hour")
    priority_s = ah.get_slot(slots, "priority")

    if not title:
        return ah.elicit("title", "What would you like to call this task?")
    if not due_str:
        return ah.elicit("due_date", f"When is {title} due?")

    due_date   = ah.parse_date(due_str)
    start_hour = ah.parse_hour(start_str) if start_str else None
    try:
        priority = max(1, min(5, int(float(priority_s)))) if priority_s else 3
    except (ValueError, TypeError):
        priority = 3

    r = Reminder(
        title=title,
        priority=priority,
        due_date=due_date,
        active_start_hour=_hour_to_dt(start_hour if start_hour is not None else 7),
        active_end_hour=_hour_to_dt(22),
        source=SOURCE,
    )
    db.session.add(r)
    db.session.commit()

    due_speech   = f", due {ah.friendly_date(due_date)}" if due_date else ""
    start_speech = f", starting at {start_hour}:00" if start_hour is not None else ""
    return ah.respond(
        f"Done! I've added \"{title}\"{due_speech}{start_speech}. "
        f"Priority set to {priority} out of 5."
    )


def _snooze_task(slots: dict) -> dict:
    title       = ah.get_slot(slots, "title")
    number      = ah.get_slot(slots, "task_number")
    duration_s  = ah.get_slot(slots, "snooze_duration")
    until_date  = ah.get_slot(slots, "snooze_until_date")
    until_time  = ah.get_slot(slots, "snooze_until_time")

    if not title and not number:
        return ah.elicit("title", "Which task would you like to snooze?")

    task, err = ah.find_task(title, number)
    if err:
        return ah.respond(err)

    now_utc = datetime.utcnow()

    if duration_s:
        delta = ah.parse_duration(duration_s)
        if not delta:
            return ah.respond(
                "I couldn't understand that duration. "
                "Try saying something like 'for 2 hours' or 'for 1 day'."
            )
        snooze_until = now_utc + delta
        until_speech = f"for {ah.duration_speech(delta)}"

    elif until_date and until_time:
        snooze_until = ah.parse_time_as_datetime(until_time, ah.parse_date(until_date))
        until_speech = f"until {ah.friendly_datetime(snooze_until)}"

    elif until_time:
        snooze_until = ah.parse_time_as_datetime(until_time)
        until_speech = f"until {ah.friendly_datetime(snooze_until)}"

    elif until_date:
        # Date only — default to the configured evening print time
        snooze_until = ah.evening_time_on(until_date)
        until_speech = f"until {ah.friendly_datetime(snooze_until)}"

    else:
        # Nothing specified — snooze until tomorrow evening
        snooze_until = ah.evening_time_on(None)
        until_speech = "until tomorrow evening"

    task.status       = "snoozed"
    task.snooze_until = snooze_until
    db.session.commit()

    return ah.respond(f"Sure! I've snoozed \"{task.title}\" {until_speech}.")


def _set_remind_at(slots: dict) -> dict:
    title       = ah.get_slot(slots, "title")
    number      = ah.get_slot(slots, "task_number")
    remind_time = ah.get_slot(slots, "remind_time")
    remind_date = ah.get_slot(slots, "remind_date")

    if not title and not number:
        return ah.elicit("title", "Which task would you like to set a reminder for?")
    if not remind_time:
        return ah.elicit("remind_time", "At what time should I remind you?")

    task, err = ah.find_task(title, number)
    if err:
        return ah.respond(err)

    on_date   = ah.parse_date(remind_date) if remind_date else None
    remind_at = ah.parse_time_as_datetime(remind_time, on_date)

    task.remind_at = remind_at
    db.session.commit()

    return ah.respond(
        f"Got it! I'll remind you about \"{task.title}\" at {ah.friendly_datetime(remind_at)}."
    )


def _delete_task(slots: dict) -> dict:
    title  = ah.get_slot(slots, "title")
    number = ah.get_slot(slots, "task_number")

    if not title and not number:
        return ah.elicit("title", "Which task would you like to delete?")

    task, err = ah.find_task(title, number)
    if err:
        return ah.respond(err)

    name = task.title
    task.deleted_at = datetime.utcnow()
    task.status = "done"
    db.session.commit()

    return ah.respond(f"Done. I've deleted \"{name}\".")


def _mark_done(slots: dict) -> dict:
    title  = ah.get_slot(slots, "title")
    number = ah.get_slot(slots, "task_number")

    if not title and not number:
        return ah.elicit("title", "Which task did you complete?")

    task, err = ah.find_task(title, number)
    if err:
        return ah.respond(err)

    name = task.title
    task.status = "done"
    db.session.commit()

    return ah.respond(f"Great work! I've marked \"{name}\" as done.")


def _list_tasks(slots: dict = None) -> dict:
    tasks = (
        Reminder.query
        .filter(Reminder.deleted_at.is_(None), Reminder.status == "active")
        .order_by(Reminder.priority.desc(), Reminder.created_at.asc())
        .all()
    )
    return ah.respond(ah.tasks_to_speech(tasks))


def _help(slots: dict = None) -> dict:
    return ah.respond(
        "Here's what I can do. "
        "To add a task, say: add buy groceries due Friday. "
        "To list tasks, say: what are my tasks. "
        "To snooze, say: snooze buy groceries for 2 hours. "
        "To set a reminder time, say: remind me at 7 p.m. to buy groceries. "
        "To mark something done, say: mark buy groceries as done. "
        "To delete, say: delete buy groceries. "
        "What would you like to do?",
        reprompt="What would you like to do?",
        end_session=False,
    )


# ── Daily summary (called by scheduler) ──────────────────────────────────────

def run_daily_summary(flask_app=None) -> None:
    """
    Query active tasks, format as speech, and announce via the HA webhook.
    Called by the APScheduler daily summary cron job.
    """
    def _do():
        tasks = (
            Reminder.query
            .filter(Reminder.deleted_at.is_(None), Reminder.status == "active")
            .order_by(Reminder.priority.desc(), Reminder.created_at.asc())
            .all()
        )
        message = "Good morning! Here is your Task Reminder summary. " + ah.tasks_to_speech(tasks)
        _ha_announce(message)

    if flask_app:
        with flask_app.app_context():
            _do()
    else:
        _do()


def _ha_announce(message: str) -> None:
    """POST a message to the Home Assistant Alexa announce webhook."""
    base  = os.environ.get("HA_WEBHOOK_BASE_URL", "").rstrip("/")
    wh_id = os.environ.get("HA_ANNOUNCE_WEBHOOK_ID", "task-reminder-announce")
    if not base:
        logger.warning("HA_WEBHOOK_BASE_URL not set — cannot send daily summary.")
        return
    url     = f"{base}/api/webhook/{wh_id}"
    payload = _json.dumps({"message": message}).encode()
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        logger.info("Daily summary sent to HA announce webhook.")
    except Exception as exc:
        logger.error("Failed to send daily summary to HA: %s", exc)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hour_to_dt(hour: int) -> datetime:
    """Convert an integer hour (NY local) to a naive UTC datetime for today."""
    now_ny = datetime.now(NY_TZ)
    target = NY_TZ.localize(
        now_ny.replace(hour=hour, minute=0, second=0, microsecond=0, tzinfo=None)
    )
    return target.astimezone(pytz.utc).replace(tzinfo=None)

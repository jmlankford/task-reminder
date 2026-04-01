"""
Helper utilities for the Alexa skill webhook.

Shared response builders, slot parsers, task-finder, and speech formatters
kept separate so alexa.py stays focused on intent logic.
"""
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import pytz

logger = logging.getLogger(__name__)
NY_TZ = pytz.timezone("America/New_York")


# ── Alexa response builders ───────────────────────────────────────────────────

def respond(speech: str, reprompt: str = None, end_session: bool = True) -> dict:
    """Build a standard Alexa JSON response envelope."""
    r: dict = {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": speech},
            "shouldEndSession": end_session,
        },
    }
    if reprompt:
        r["response"]["reprompt"] = {
            "outputSpeech": {"type": "PlainText", "text": reprompt}
        }
    return r


def elicit(slot_name: str, speech: str) -> dict:
    """Ask Alexa to prompt the user for a specific missing slot."""
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": speech},
            "directives": [{"type": "Dialog.ElicitSlot", "slotToElicit": slot_name}],
            "shouldEndSession": False,
        },
    }


# ── Slot value extraction ─────────────────────────────────────────────────────

def get_slot(slots: dict, name: str) -> Optional[str]:
    """
    Safely extract a slot value. Checks entity resolution values first
    (for custom slot types), then falls back to the raw spoken value.
    """
    slot = slots.get(name, {})
    for res in slot.get("resolutions", {}).get("resolutionsPerAuthority", []):
        if res.get("status", {}).get("code") == "ER_SUCCESS_MATCH":
            vals = res.get("values", [])
            if vals:
                return vals[0]["value"]["name"]
    val = slot.get("value")
    return val.strip() if val else None


# ── Date / time parsing ───────────────────────────────────────────────────────

def parse_date(date_str: str) -> Optional[datetime]:
    """
    Convert an Alexa AMAZON.DATE string to a naive UTC datetime set to 11:59 PM
    on that date in NY time.

    Alexa formats:
      YYYY-MM-DD   — specific date
      YYYY-Www     — ISO week  (we use Friday of that week)
      YYYY-MM      — month ref (we use the 28th)
      PRESENT_REF / FUTURE_REF — ignored (returns None)
    """
    if not date_str or date_str in ("PRESENT_REF", "FUTURE_REF"):
        return None
    try:
        if "W" in date_str:
            d = datetime.strptime(date_str + "-5", "%Y-W%W-%w")   # Friday
        elif len(date_str) == 7:
            d = datetime.strptime(date_str, "%Y-%m").replace(day=28)
        else:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        ny_dt = NY_TZ.localize(d.replace(hour=23, minute=59, second=0, microsecond=0))
        return ny_dt.astimezone(pytz.utc).replace(tzinfo=None)
    except Exception:
        logger.warning("Cannot parse Alexa date: %r", date_str)
        return None


def parse_time_as_datetime(
    time_str: str, on_date: Optional[datetime] = None
) -> Optional[datetime]:
    """
    Convert an Alexa AMAZON.TIME value to a naive UTC datetime.

    Alexa special tokens: MO (morning/8am), AF (afternoon/1pm),
                          EV (evening/6pm), NI (night/9pm).

    If on_date is provided (naive UTC), uses that calendar date.
    Otherwise defaults to today in NY time. Rolls forward one day
    if the resulting time is already in the past.
    """
    if not time_str:
        return None
    tokens = {"MO": "08:00", "AF": "13:00", "EV": "18:00", "NI": "21:00"}
    time_str = tokens.get(time_str, time_str)
    try:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        logger.warning("Cannot parse Alexa time: %r", time_str)
        return None

    now_ny = datetime.now(NY_TZ)
    if on_date:
        ny_base = pytz.utc.localize(on_date).astimezone(NY_TZ)
        base = NY_TZ.localize(
            ny_base.replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=None)
        )
    else:
        base = now_ny.replace(hour=h, minute=m, second=0, microsecond=0)

    if base <= now_ny:
        base += timedelta(days=1)

    return base.astimezone(pytz.utc).replace(tzinfo=None)


def parse_duration(duration_str: str) -> Optional[timedelta]:
    """
    Parse an ISO 8601 duration string (PT30M, P1D, PT2H30M …) into a timedelta.
    Returns None if the string is blank or unrecognisable.
    """
    if not duration_str:
        return None
    pat = (
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
        r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?"
    )
    m = re.match(pat, duration_str)
    if not m:
        return None
    yr, mo, wk, dy, hr, mn, sc = (int(v) if v else 0 for v in m.groups())
    return timedelta(
        days=yr * 365 + mo * 30 + wk * 7 + dy,
        hours=hr, minutes=mn, seconds=sc,
    )


def parse_hour(time_str: str) -> Optional[int]:
    """Extract only the hour (0–23) from an Alexa AMAZON.TIME string."""
    tokens = {"MO": 8, "AF": 13, "EV": 18, "NI": 21}
    if time_str in tokens:
        return tokens[time_str]
    try:
        return int(time_str.split(":")[0])
    except (ValueError, AttributeError):
        return None


def evening_time_on(date_str: Optional[str]) -> datetime:
    """
    Return naive UTC datetime for RECEIPT_EVENING_TIME on the given Alexa date
    string, or tomorrow evening if date_str is None.

    Used when a user says "snooze until Friday" without specifying a time —
    we default to the evening print time for that day.
    """
    raw = os.environ.get("RECEIPT_EVENING_TIME", "19:30")
    try:
        ev_h, ev_m = (int(x) for x in raw.split(":"))
    except Exception:
        ev_h, ev_m = 19, 30

    now_ny = datetime.now(NY_TZ)
    if date_str:
        base_utc = parse_date(date_str)
        if base_utc:
            ny_base = pytz.utc.localize(base_utc).astimezone(NY_TZ)
            target = NY_TZ.localize(
                ny_base.replace(
                    hour=ev_h, minute=ev_m, second=0, microsecond=0, tzinfo=None
                )
            )
            return target.astimezone(pytz.utc).replace(tzinfo=None)

    # Default: next evening
    target = now_ny.replace(hour=ev_h, minute=ev_m, second=0, microsecond=0)
    if target <= now_ny:
        target += timedelta(days=1)
    return target.astimezone(pytz.utc).replace(tzinfo=None)


# ── Task lookup ───────────────────────────────────────────────────────────────

def find_task(title: Optional[str], number: Optional[str]):
    """
    Locate a Reminder by fuzzy title match or 1-based index in the active list.

    Returns (Reminder, None) on success or (None, error_speech) on failure.
    Searches active, snoozed, and scheduled tasks.
    """
    from .models import Reminder

    candidate_statuses = ["active", "snoozed", "scheduled"]

    if number:
        try:
            idx = int(float(number))
        except (ValueError, TypeError):
            return None, "I couldn't understand that task number."
        tasks = (
            Reminder.query
            .filter(Reminder.deleted_at.is_(None), Reminder.status.in_(candidate_statuses))
            .order_by(Reminder.priority.desc(), Reminder.created_at.asc())
            .all()
        )
        if idx < 1 or idx > len(tasks):
            return None, f"There is no task number {idx}. You have {len(tasks)} tasks."
        return tasks[idx - 1], None

    if title:
        title_l = title.lower().strip()
        all_tasks = Reminder.query.filter(
            Reminder.deleted_at.is_(None),
            Reminder.status.in_(candidate_statuses),
        ).all()
        for t in all_tasks:            # exact match
            if t.title.lower() == title_l:
                return t, None
        for t in all_tasks:            # starts with
            if t.title.lower().startswith(title_l):
                return t, None
        for t in all_tasks:            # contains
            if title_l in t.title.lower():
                return t, None
        return None, f"I couldn't find a task matching \"{title}\". Try listing your tasks first."

    return None, "I need a task title or number."


# ── Speech formatting ─────────────────────────────────────────────────────────

def friendly_date(dt: Optional[datetime]) -> str:
    """Format a naive UTC datetime as 'Wednesday April 1' in NY time."""
    if not dt:
        return "no date set"
    ny = pytz.utc.localize(dt).astimezone(NY_TZ)
    return ny.strftime("%A %B %d").replace(" 0", " ")


def friendly_datetime(dt: Optional[datetime]) -> str:
    """Format a naive UTC datetime as 'Wednesday April 1 at 7 PM' in NY time."""
    if not dt:
        return "no time set"
    ny = pytz.utc.localize(dt).astimezone(NY_TZ)
    s = ny.strftime("%A %B %d at %I:%M %p").replace(" 0", " ")
    # Drop :00 → "7:00 PM" becomes "7 PM"
    s = re.sub(r":00 (AM|PM)", r" \1", s)
    return s.lstrip("0")


def duration_speech(delta: timedelta) -> str:
    """Convert a timedelta to a natural speech string like '2 hours' or '3 days'."""
    total = int(delta.total_seconds())
    if total >= 86400:
        d = total // 86400
        return f"{d} {'day' if d == 1 else 'days'}"
    if total >= 3600:
        h = total // 3600
        return f"{h} {'hour' if h == 1 else 'hours'}"
    mn = max(1, total // 60)
    return f"{mn} {'minute' if mn == 1 else 'minutes'}"


def tasks_to_speech(tasks: list) -> str:
    """
    Format a list of Reminder objects as natural speech.
    Includes title, due date, and overdue flag for each task.
    """
    if not tasks:
        return "You have no active tasks right now. All clear!"
    n = len(tasks)
    parts = [f"You have {n} active {'task' if n == 1 else 'tasks'}."]
    for i, t in enumerate(tasks, 1):
        due = f", due {friendly_date(t.due_date)}" if t.due_date else ""
        flag = " This one is overdue." if t.overdue else ""
        parts.append(f"Task {i}: {t.title}{due}.{flag}")
    return " ".join(parts)

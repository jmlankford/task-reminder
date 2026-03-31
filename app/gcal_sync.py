"""
Google Calendar sync — Part 4.

Pulls events from one or more calendars via a Service Account.
Any event with "[reminder]" in the title or description is upserted
into the reminders table.  Events that disappear from Google are
soft-deleted here.

Environment variables
---------------------
GCAL_SERVICE_ACCOUNT_JSON   Path to the service-account key file
                             (default: /data/gcal_service_account.json)
GCAL_CALENDAR_IDS           Comma-separated calendar IDs to watch
                             (default: primary)
GCAL_LOOKAHEAD_HOURS        How many hours ahead to fetch (default: 24)
GCAL_API_KEY                Optional static key for POST /gcal/sync
                             (if unset the endpoint is unauthenticated)
"""

import logging
import os
import re
from datetime import datetime, timedelta, date

import pytz

from .models import db, Reminder
from .utils import now_utc

logger = logging.getLogger(__name__)

NY_TZ = pytz.timezone("America/New_York")
REMINDER_TAG = re.compile(r"\[reminder\]", re.IGNORECASE)

# ── helpers ───────────────────────────────────────────────────────────────────

def _build_service():
    """Return a Google Calendar service object using the service-account key."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key_path = os.environ.get(
        "GCAL_SERVICE_ACCOUNT_JSON", "/data/gcal_service_account.json"
    )
    if not os.path.exists(key_path):
        raise FileNotFoundError(
            f"Service account key not found at {key_path}. "
            "Set GCAL_SERVICE_ACCOUNT_JSON to the correct path."
        )

    credentials = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _has_tag(event: dict) -> bool:
    """Return True if the event title or description contains [reminder]."""
    title = event.get("summary", "")
    desc  = event.get("description", "") or ""
    return bool(REMINDER_TAG.search(title) or REMINDER_TAG.search(desc))


def _strip_tag(text: str) -> str:
    """Remove [reminder] from a string (case-insensitive)."""
    return REMINDER_TAG.sub("", text).strip()


def _parse_event(event: dict) -> dict:
    """
    Extract fields from a Google Calendar event dict.

    Returns a dict with keys:
        gcal_event_id, title, due_date, active_start_hour, active_end_hour,
        notes_details, priority
    """
    gcal_id = event["id"]
    raw_title = event.get("summary", "(no title)")
    title = _strip_tag(raw_title) or "(no title)"

    # ── Dates / times ────────────────────────────────────────────────────────
    start_raw = event.get("start", {})
    end_raw   = event.get("end", {})

    if "dateTime" in start_raw:
        # Timed event — parse ISO 8601 with timezone, convert to UTC-naive
        start_dt = _parse_iso(start_raw["dateTime"])
        end_dt   = _parse_iso(end_raw["dateTime"])
    else:
        # All-day event — Google's end.date is exclusive (day after last day)
        start_d = date.fromisoformat(start_raw["date"])
        end_d   = date.fromisoformat(end_raw["date"]) - timedelta(days=1)
        # Localise to NY then strip timezone for naive UTC storage
        start_dt = NY_TZ.localize(
            datetime.combine(start_d, datetime.min.time())
        ).astimezone(pytz.utc).replace(tzinfo=None)
        end_dt = NY_TZ.localize(
            datetime.combine(end_d, datetime.max.replace(microsecond=0).time())
        ).astimezone(pytz.utc).replace(tzinfo=None)

    notes = _strip_tag(event.get("description", "") or "").strip() or None

    return {
        "gcal_event_id":    gcal_id,
        "title":            title,
        "active_start_hour": start_dt,
        "active_end_hour":  end_dt,
        "due_date":         end_dt,
        "notes_details":    notes,
        "priority":         1,        # gcal events default to lowest priority
    }


def _parse_iso(iso_str: str) -> datetime:
    """Parse an ISO-8601 datetime string to a naive UTC datetime."""
    # Python 3.7+ fromisoformat doesn't handle the trailing 'Z'
    iso_str = iso_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is not None:
        dt = dt.astimezone(pytz.utc).replace(tzinfo=None)
    return dt


def _fetch_events(service, calendar_id: str, time_min: datetime, time_max: datetime) -> list:
    """Fetch all events from a single calendar within [time_min, time_max]."""
    events = []
    page_token = None
    while True:
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.strftime("%Y-%m-%dT%H:%M:%SZ"),
            timeMax=time_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
        ).execute()
        events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return events


# ── main sync logic ───────────────────────────────────────────────────────────

def _do_sync() -> dict:
    """
    Core sync logic. Must be called inside a Flask app context.
    Returns a summary dict: {created, updated, deleted, skipped, errors}.
    """
    service = _build_service()

    calendar_ids_raw = os.environ.get("GCAL_CALENDAR_IDS", "primary")
    calendar_ids = [c.strip() for c in calendar_ids_raw.split(",") if c.strip()]

    lookahead_hours = float(os.environ.get("GCAL_LOOKAHEAD_HOURS", 24))
    now = now_utc()
    time_min = now - timedelta(hours=48)   # include recently-started events
    time_max = now + timedelta(hours=lookahead_hours)

    # Gather all tagged events across all calendars
    tagged_events: list[dict] = []
    for cal_id in calendar_ids:
        try:
            raw = _fetch_events(service, cal_id, time_min, time_max)
            tagged_events.extend(e for e in raw if _has_tag(e))
        except Exception:
            logger.exception("Error fetching events from calendar %s", cal_id)

    seen_ids: set[str] = set()
    created = updated = deleted = skipped = errors = 0

    for event in tagged_events:
        try:
            parsed = _parse_event(event)
            gcal_id = parsed["gcal_event_id"]
            seen_ids.add(gcal_id)

            existing: Reminder | None = Reminder.query.filter_by(
                gcal_event_id=gcal_id
            ).first()

            if existing is None:
                # New event → create reminder
                r = Reminder(
                    source="gcal",
                    status="active",
                    **parsed,
                )
                db.session.add(r)
                created += 1
                logger.debug("Created reminder for gcal event %s: %s", gcal_id, parsed["title"])
            else:
                # Existing — restore if soft-deleted, update fields
                if existing.deleted_at is not None:
                    existing.deleted_at = None
                    existing.status = "active"

                # Only update title, dates, and notes — respect user-set priority/status
                existing.title            = parsed["title"]
                existing.active_start_hour = parsed["active_start_hour"]
                existing.active_end_hour  = parsed["active_end_hour"]
                existing.due_date         = parsed["due_date"]
                if parsed["notes_details"] is not None:
                    existing.notes_details = parsed["notes_details"]
                updated += 1
                logger.debug("Updated reminder for gcal event %s", gcal_id)

        except Exception:
            logger.exception("Error processing gcal event %s", event.get("id"))
            errors += 1

    # ── Deletion detection ────────────────────────────────────────────────────
    # Find gcal-sourced reminders whose start time falls in the fetch window
    # but whose gcal_event_id was NOT returned — event was deleted on Google.
    candidates: list[Reminder] = Reminder.query.filter(
        Reminder.source == "gcal",
        Reminder.deleted_at.is_(None),
        Reminder.active_start_hour >= time_min,
        Reminder.active_start_hour <= time_max,
    ).all()

    for r in candidates:
        if r.gcal_event_id and r.gcal_event_id not in seen_ids:
            r.deleted_at = now
            deleted += 1
            logger.debug("Soft-deleted reminder for removed gcal event %s", r.gcal_event_id)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Error committing gcal sync")
        raise

    summary = {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "skipped": skipped,
        "errors":  errors,
    }
    logger.info("GCal sync complete: %s", summary)
    return summary


def run_sync(flask_app=None) -> dict:
    """
    Public entry point.  Call with flask_app from the scheduler (needs its own
    app context); call without it from within a request (already in context).
    """
    if flask_app is not None:
        with flask_app.app_context():
            return _do_sync()
    else:
        return _do_sync()

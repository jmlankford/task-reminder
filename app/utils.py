import pytz
from datetime import datetime

NY_TZ = pytz.timezone("America/New_York")


def now_utc() -> datetime:
    """Return the current time as a naive UTC datetime (matches SQLite storage)."""
    return datetime.utcnow()


def now_ny() -> datetime:
    """Return the current time in America/New_York (timezone-aware)."""
    return datetime.now(NY_TZ)


def parse_datetime(value) -> datetime | None:
    """
    Parse an ISO 8601 datetime string into a naive UTC datetime for DB storage.
    Naive inputs are treated as America/New_York and converted to UTC.
    Returns None if value is None or unparseable.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            # Treat as NY local time, convert to UTC
            dt = NY_TZ.localize(dt).astimezone(pytz.utc).replace(tzinfo=None)
        else:
            dt = dt.astimezone(pytz.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def get_max_active() -> int:
    from .models import Config
    config = Config.query.get("max_active")
    return int(config.value) if config else 5


def get_active_count() -> int:
    """Count reminders occupying an active slot (status active or snoozed, not deleted)."""
    from .models import Reminder
    return Reminder.query.filter(
        Reminder.status.in_(["active", "snoozed"]),
        Reminder.deleted_at.is_(None),
    ).count()


def promote_inactive_tasks(db) -> int:
    """
    Promote the highest-priority inactive reminders into available slots.
    Tie-breaks by oldest created_at.
    Returns the number of reminders promoted.
    """
    from .models import Reminder
    promoted = 0
    while True:
        if get_active_count() >= get_max_active():
            break
        next_task = (
            Reminder.query.filter(
                Reminder.status == "inactive",
                Reminder.deleted_at.is_(None),
            )
            .order_by(Reminder.priority.desc(), Reminder.created_at.asc())
            .first()
        )
        if not next_task:
            break
        next_task.status = "active"
        db.session.commit()
        promoted += 1
    return promoted

from datetime import timedelta
from flask import Blueprint, request, jsonify
from ..models import db, Reminder, VALID_STATUSES
from ..utils import (
    now_utc,
    parse_datetime,
    get_max_active,
    get_active_count,
    promote_inactive_tasks,
)

reminders_bp = Blueprint("reminders", __name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def ok(data=None, message=None, status_code=200):
    resp = {"success": True}
    if data is not None:
        resp["data"] = data
    if message:
        resp["message"] = message
    return jsonify(resp), status_code


def err(message, status_code=400):
    return jsonify({"success": False, "error": message}), status_code


def _resolve_status(active_start: object, now: object) -> str:
    """Determine initial status for a new reminder based on slot availability."""
    if active_start > now:
        return "scheduled"
    if get_active_count() < get_max_active():
        return "active"
    return "inactive"


# ── GET /reminders ────────────────────────────────────────────────────────────

@reminders_bp.route("/reminders", methods=["GET"])
def get_active_reminders():
    """Return only reminders with status='active', ordered by priority desc then age asc."""
    reminders = (
        Reminder.query.filter(
            Reminder.status == "active",
            Reminder.deleted_at.is_(None),
        )
        .order_by(Reminder.priority.desc(), Reminder.created_at.asc())
        .all()
    )
    return ok([r.to_dict() for r in reminders])


# ── GET /reminders/all ────────────────────────────────────────────────────────

@reminders_bp.route("/reminders/all", methods=["GET"])
def get_all_reminders():
    """Return every reminder including soft-deleted records."""
    reminders = (
        Reminder.query
        .order_by(Reminder.created_at.desc())
        .all()
    )
    return ok([r.to_dict() for r in reminders])


# ── POST /reminders ───────────────────────────────────────────────────────────

@reminders_bp.route("/reminders", methods=["POST"])
def create_reminder():
    data = request.get_json(silent=True)
    if not data:
        return err("Request body must be JSON.")
    if not data.get("title", "").strip():
        return err("title is required.")

    priority = data.get("priority", 1)
    if not isinstance(priority, int) or not (1 <= priority <= 5):
        return err("priority must be an integer from 1 (lowest) to 5 (highest).")

    now = now_utc()

    active_start = parse_datetime(data.get("active_start_hour")) or now
    active_end = parse_datetime(data.get("active_end_hour"))
    due_date = parse_datetime(data.get("due_date"))
    remind_at = parse_datetime(data.get("remind_at"))

    # Cross-fill: whichever of active_end / due_date is missing inherits the other
    if active_end and not due_date:
        due_date = active_end
    elif due_date and not active_end:
        active_end = due_date

    status = _resolve_status(active_start, now)

    reminder = Reminder(
        title=data["title"].strip(),
        priority=priority,
        active_start_hour=active_start,
        active_end_hour=active_end,
        due_date=due_date,
        source=data.get("source", "manual"),
        notes_details=data.get("notes_details") or None,
        remind_at=remind_at,
        status=status,
        created_at=now,
    )
    db.session.add(reminder)
    db.session.commit()

    message = None
    if status == "inactive":
        message = (
            "Active slots are full. Reminder saved as Inactive and will be "
            "promoted automatically when a slot opens."
        )

    return ok(reminder.to_dict(), message=message, status_code=201)


# ── POST /reminders/<id>/done ─────────────────────────────────────────────────

@reminders_bp.route("/reminders/<int:reminder_id>/done", methods=["POST"])
def mark_done(reminder_id):
    reminder = Reminder.query.filter_by(id=reminder_id, deleted_at=None).first()
    if not reminder:
        return err("Reminder not found.", 404)
    if reminder.status == "done":
        return err("Reminder is already marked as done.")

    was_occupying_slot = reminder.status in ("active", "snoozed")
    reminder.status = "done"
    db.session.commit()

    if was_occupying_slot:
        promote_inactive_tasks(db)

    return ok(reminder.to_dict(), message="Reminder marked as done.")


# ── POST /reminders/<id>/snooze ───────────────────────────────────────────────

@reminders_bp.route("/reminders/<int:reminder_id>/snooze", methods=["POST"])
def snooze_reminder(reminder_id):
    reminder = Reminder.query.filter_by(id=reminder_id, deleted_at=None).first()
    if not reminder:
        return err("Reminder not found.", 404)
    if reminder.status not in ("active", "snoozed"):
        return err(
            f'Only active or already-snoozed reminders can be snoozed '
            f'(current status: "{reminder.status}").'
        )

    try:
        hours = float(request.args.get("hours", 1))
    except (ValueError, TypeError):
        return err("hours must be a number (e.g. ?hours=2).")
    if hours <= 0:
        return err("hours must be greater than 0.")

    reminder.status = "snoozed"
    reminder.snooze_until = now_utc() + timedelta(hours=hours)
    db.session.commit()

    return ok(
        reminder.to_dict(),
        message=f"Reminder snoozed for {hours} hour(s). Reactivates at {reminder.snooze_until.isoformat()}Z.",
    )


# ── POST /reminders/<id>/activate ────────────────────────────────────────────

@reminders_bp.route("/reminders/<int:reminder_id>/activate", methods=["POST"])
def activate_reminder(reminder_id):
    reminder = Reminder.query.filter_by(id=reminder_id, deleted_at=None).first()
    if not reminder:
        return err("Reminder not found.", 404)
    if reminder.status == "active":
        return err("Reminder is already active.")
    if reminder.status == "done":
        return err('Cannot activate a completed reminder. Use PATCH /reminders/<id> to change its status.')

    if get_active_count() >= get_max_active():
        reminder.status = "inactive"
        db.session.commit()
        return ok(
            reminder.to_dict(),
            message=(
                "Active slots are full. Reminder saved as Inactive and will be "
                "promoted automatically when a slot opens."
            ),
        )

    reminder.status = "active"
    reminder.snooze_until = None
    db.session.commit()
    return ok(reminder.to_dict(), message="Reminder is now active.")


# ── PATCH /reminders/<id> ─────────────────────────────────────────────────────

@reminders_bp.route("/reminders/<int:reminder_id>", methods=["PATCH"])
def update_reminder(reminder_id):
    reminder = Reminder.query.filter_by(id=reminder_id, deleted_at=None).first()
    if not reminder:
        return err("Reminder not found.", 404)

    data = request.get_json(silent=True)
    if not data:
        return err("Request body must be JSON.")

    datetime_fields = {"active_start_hour", "active_end_hour", "due_date", "snooze_until", "remind_at"}

    for field, value in data.items():
        if field == "title":
            if not str(value).strip():
                return err("title cannot be empty.")
            reminder.title = str(value).strip()

        elif field == "priority":
            if not isinstance(value, int) or not (1 <= value <= 5):
                return err("priority must be an integer from 1 (lowest) to 5 (highest).")
            reminder.priority = value

        elif field == "status":
            if value not in VALID_STATUSES:
                return err(f'Invalid status. Valid values: {", ".join(sorted(VALID_STATUSES))}.')
            reminder.status = value

        elif field == "overdue":
            if not isinstance(value, bool):
                return err("overdue must be a boolean.")
            reminder.overdue = value

        elif field in datetime_fields:
            reminder.__setattr__(field, parse_datetime(value))

        elif field == "source":
            reminder.source = str(value)

        elif field == "notes_details":
            reminder.notes_details = str(value) if value is not None else None

        else:
            return err(f'Field "{field}" is not patchable.')

    db.session.commit()
    return ok(reminder.to_dict(), message="Reminder updated.")


# ── DELETE /reminders/<id> ────────────────────────────────────────────────────

@reminders_bp.route("/reminders/<int:reminder_id>", methods=["DELETE"])
def delete_reminder(reminder_id):
    reminder = Reminder.query.filter_by(id=reminder_id, deleted_at=None).first()
    if not reminder:
        return err("Reminder not found.", 404)

    was_occupying_slot = reminder.status in ("active", "snoozed")
    reminder.deleted_at = now_utc()
    db.session.commit()

    if was_occupying_slot:
        promote_inactive_tasks(db)

    return ok(reminder.to_dict(), message="Reminder soft-deleted.")

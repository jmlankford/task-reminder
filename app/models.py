from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

VALID_STATUSES = {"active", "snoozed", "done", "inactive", "scheduled", "inactive_passed"}


class Reminder(db.Model):
    __tablename__ = "reminders"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    priority = db.Column(db.Integer, nullable=False, default=1)  # 1=lowest, 5=highest
    active_start_hour = db.Column(db.DateTime, nullable=True)
    active_end_hour = db.Column(db.DateTime, nullable=True)
    due_date = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(100), nullable=False, default="manual")
    snooze_until = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="active")
    overdue = db.Column(db.Boolean, nullable=False, default=False)
    notes_details = db.Column(db.Text, nullable=True)
    last_notified_at = db.Column(db.DateTime, nullable=True)
    gcal_event_id = db.Column(db.String(255), nullable=True, index=True)
    remind_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "priority": self.priority,
            "active_start_hour": self.active_start_hour.isoformat() if self.active_start_hour else None,
            "active_end_hour": self.active_end_hour.isoformat() if self.active_end_hour else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "source": self.source,
            "snooze_until": self.snooze_until.isoformat() if self.snooze_until else None,
            "status": self.status,
            "overdue": self.overdue,
            "notes_details": self.notes_details,
            "last_notified_at": self.last_notified_at.isoformat() if self.last_notified_at else None,
            "gcal_event_id": self.gcal_event_id,
            "remind_at": self.remind_at.isoformat() if self.remind_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Config(db.Model):
    __tablename__ = "config"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(500), nullable=False)

    def to_dict(self):
        return {"key": self.key, "value": self.value}

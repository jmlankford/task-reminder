import logging
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def init_scheduler(app):
    import os
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        func=lambda: _run_tick(app),
        trigger="interval",
        seconds=60,
        id="main_tick",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # Only enable gcal sync if credentials are configured
    if os.environ.get("GCAL_SERVICE_ACCOUNT_JSON") or os.path.exists(
        "/data/gcal_service_account.json"
    ):
        scheduler.add_job(
            func=lambda: _run_gcal_sync(app),
            trigger="interval",
            minutes=30,
            id="gcal_sync",
            replace_existing=True,
            misfire_grace_time=120,
        )
        logger.info("GCal sync job scheduled (30-minute interval).")
    else:
        logger.info("GCAL_SERVICE_ACCOUNT_JSON not configured — GCal sync disabled.")

    # Receipt print jobs — only enabled when RECEIPT_PRINTER_IP is set
    if os.environ.get("RECEIPT_PRINTER_IP"):
        morning_h, morning_m = _parse_hm("RECEIPT_MORNING_TIME", "07:30")
        evening_h, evening_m = _parse_hm("RECEIPT_EVENING_TIME", "19:30")

        scheduler.add_job(
            func=lambda: _run_receipt_print(app, is_morning=True),
            trigger="cron",
            hour=morning_h,
            minute=morning_m,
            timezone="America/New_York",
            id="receipt_morning",
            replace_existing=True,
            misfire_grace_time=300,
        )
        scheduler.add_job(
            func=lambda: _run_receipt_print(app, is_morning=False),
            trigger="cron",
            hour=evening_h,
            minute=evening_m,
            timezone="America/New_York",
            id="receipt_evening",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info(
            "Receipt print jobs scheduled: %02d:%02d (morning) and %02d:%02d (evening) NY time.",
            morning_h, morning_m, evening_h, evening_m,
        )
    else:
        logger.info("RECEIPT_PRINTER_IP not set — receipt print jobs disabled.")

    scheduler.start()
    logger.info("Background scheduler started (60s interval).")
    return scheduler


def _run_tick(app):
    """
    Runs every 60 seconds. Handles:
      1. scheduled   → active        (when active_start_hour arrives; bypasses cap)
      2. active      → inactive_passed (when active_end_hour passes)
      3. snoozed     → active        (when snooze_until lapses)
      4. overdue flag set            (when due_date passes)
      5. Promote inactive tasks to fill newly freed slots
    """
    with app.app_context():
        from .models import db, Reminder
        from .utils import promote_inactive_tasks, now_utc

        now = now_utc()
        freed_slots = 0

        try:
            # ── 1. scheduled → active (ignores the active cap) ──────────────────
            scheduled = Reminder.query.filter(
                Reminder.status == "scheduled",
                Reminder.deleted_at.is_(None),
                Reminder.active_start_hour <= now,
            ).all()
            for task in scheduled:
                task.status = "active"

            # ── 2. active → inactive_passed (active_end_hour has passed) ────────
            passed = Reminder.query.filter(
                Reminder.status == "active",
                Reminder.deleted_at.is_(None),
                Reminder.active_end_hour.isnot(None),
                Reminder.active_end_hour <= now,
            ).all()
            for task in passed:
                task.status = "inactive_passed"
                freed_slots += 1

            # ── 3. snoozed → active (snooze_until has lapsed) ───────────────────
            unsnoozed = Reminder.query.filter(
                Reminder.status == "snoozed",
                Reminder.deleted_at.is_(None),
                Reminder.snooze_until <= now,
            ).all()
            for task in unsnoozed:
                task.status = "active"
                task.snooze_until = None

            # ── 4. Mark overdue (due_date passed, not done/deleted) ──────────────
            Reminder.query.filter(
                Reminder.due_date.isnot(None),
                Reminder.due_date <= now,
                Reminder.overdue == False,  # noqa: E712
                Reminder.deleted_at.is_(None),
                Reminder.status.notin_(["done"]),
            ).update({"overdue": True}, synchronize_session=False)

            db.session.commit()

            # ── 5. Promote inactive tasks into freed slots ───────────────────────
            if freed_slots > 0:
                promote_inactive_tasks(db)

        except Exception:
            db.session.rollback()
            logger.exception("Error during scheduler tick")
        finally:
            db.session.remove()


def _parse_hm(env_var: str, default: str) -> tuple[int, int]:
    """Parse an HH:MM env var, falling back to the given default string."""
    import os
    raw = os.environ.get(env_var, default)
    try:
        h, m = raw.strip().split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        logger.warning("Invalid %s=%r — using default %s", env_var, raw, default)
        h, m = default.split(":")
        return int(h), int(m)


def _run_receipt_print(app, is_morning: bool) -> None:
    """Wrapper for the scheduled receipt print jobs."""
    try:
        from .receipt_printer import run_print
        run_print(flask_app=app, is_morning=is_morning)
    except Exception:
        logger.exception("Unhandled error in receipt print job")


def _run_gcal_sync(app):
    """Wrapper for the 30-minute GCal sync job."""
    try:
        from .gcal_sync import run_sync
        run_sync(flask_app=app)
    except Exception:
        logger.exception("Error during GCal sync job")

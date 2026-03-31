"""
Telegram bot for Task Reminders.

Runs in a background daemon thread with its own asyncio event loop so it
doesn't interfere with the synchronous Flask/Gunicorn process.

Supported message syntax (with or without a leading '/'):
  list                    — numbered list of active + snoozed reminders
  add [title]             — create reminder, default priority 3
  add [title] p:N         — create with priority N (1–5)
  add [title] due:today   — due tonight 11:59 PM (America/New_York)
  add [title] due:tomorrow— due tomorrow  11:59 PM
  N done                  — mark reminder #N done
  N snooze                — snooze reminder #N for 1 hour
  N snooze H              — snooze for H hours (decimals OK, e.g. 4.5)
  help                    — show this command list
"""

import asyncio
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from typing import Optional

import pytz
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)

NY_TZ = pytz.timezone("America/New_York")

# ── Module-level state ─────────────────────────────────────────────────────
_bot_app: Optional[Application] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None
# Maps chat_id → ordered list of reminder IDs matching the last shown numbered list
_last_list: dict[int, list[int]] = {}
_allowed_ids: set[str] = set()


# ── Public helpers (callable from sync Flask code) ─────────────────────────

def send_message_sync(chat_id: int, text: str, parse_mode: str = "Markdown") -> None:
    """Send a Telegram message from a synchronous (Flask) context."""
    if _bot_app is None or _bot_loop is None:
        logger.warning("Telegram bot not ready — message dropped.")
        return
    future = asyncio.run_coroutine_threadsafe(
        _bot_app.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode),
        _bot_loop,
    )
    try:
        future.result(timeout=10)
    except Exception as exc:
        logger.error("Failed to send Telegram message: %s", exc)


def set_last_list(chat_id: int, reminder_ids: list[int]) -> None:
    """Store the ordered reminder IDs for a chat so 'N done/snooze' can resolve them."""
    _last_list[chat_id] = list(reminder_ids)


def get_last_list(chat_id: int) -> list[int]:
    return _last_list.get(chat_id, [])


# ── Bot startup ────────────────────────────────────────────────────────────

def start_bot_thread(flask_app, token: str) -> None:
    """Spawn the daemon thread that owns the bot's asyncio event loop."""
    global _allowed_ids

    raw = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
    _allowed_ids = {x.strip() for x in raw.split(",") if x.strip()}

    def _run() -> None:
        global _bot_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _bot_loop = loop  # expose BEFORE blocking so Flask can use it immediately
        try:
            loop.run_until_complete(_run_bot(flask_app, token))
        except Exception:
            logger.exception("Telegram bot thread crashed.")

    thread = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    thread.start()
    logger.info("Telegram bot thread started.")


async def _run_bot(flask_app, token: str) -> None:
    global _bot_app

    _bot_app = Application.builder().token(token).build()
    _bot_app.add_handler(MessageHandler(filters.TEXT, _make_handler(flask_app)))

    await _bot_app.initialize()
    await _bot_app.start()
    await _bot_app.updater.start_polling(drop_pending_updates=True)

    logger.info("Telegram bot polling started.")
    await asyncio.Event().wait()  # block until process exits


# ── Master message handler factory ────────────────────────────────────────

def _make_handler(flask_app):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        user_id = str(update.effective_user.id)
        if _allowed_ids and user_id not in _allowed_ids:
            return  # silently drop unauthorised users

        raw = update.message.text.strip()
        chat_id = update.effective_chat.id

        # Normalise: strip a leading '/' so /list == list, /help == help, etc.
        text = raw[1:] if raw.startswith("/") else raw
        lower = text.lower()

        try:
            if lower in ("list", "ls"):
                await _cmd_list(update, flask_app)
            elif lower in ("help", "h", "?", "start"):
                await _cmd_help(update)
            elif re.match(r"^add(\s|$)", lower):
                await _cmd_add(update, flask_app, text)
            elif re.match(r"^\d+\s+done$", lower):
                await _cmd_done(update, flask_app, lower)
            elif re.match(r"^\d+\s+snooze(\s+[\d.]+)?$", lower):
                await _cmd_snooze(update, flask_app, lower)
            else:
                await update.message.reply_text(
                    "❓ Unknown command. Type *help* to see what's available.",
                    parse_mode="Markdown",
                )
        except Exception:
            logger.exception("Error handling Telegram message from user %s", user_id)
            await update.message.reply_text("⚠️ Something went wrong. Please try again.")

    return handler


# ── Command handlers ───────────────────────────────────────────────────────

async def _cmd_help(update: Update) -> None:
    msg = (
        "📋 *Task Reminders — Commands*\n\n"
        "*Viewing:*\n"
        "• `list` — active & snoozed reminders\n\n"
        "*Adding:*\n"
        "• `add Buy milk` — priority 3, starts now\n"
        "• `add Buy milk p:5` — with priority 1–5 _(1=low, 5=high)_\n"
        "• `add Buy milk due:today` — due tonight 11:59 PM\n"
        "• `add Buy milk due:tomorrow` — due tomorrow 11:59 PM\n"
        "• `add Buy milk remind:14:30` — toast/print reminder at 2:30 PM\n"
        "• Options can be combined: `add Task p:4 due:tomorrow remind:09:00`\n\n"
        "*Acting on a reminder:*\n"
        "• `1 done` — mark #1 as done\n"
        "• `1 snooze` — snooze #1 for 1 hour\n"
        "• `1 snooze 4.5` — snooze for 4.5 hours _(decimals OK)_\n\n"
        "Numbers refer to the last list shown. Type `list` to refresh."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def _cmd_list(update: Update, flask_app) -> None:
    chat_id = update.effective_chat.id

    with flask_app.app_context():
        from .models import Reminder

        reminders = (
            Reminder.query.filter(
                Reminder.status.in_(["active", "snoozed"]),
                Reminder.deleted_at.is_(None),
            )
            .order_by(
                Reminder.overdue.desc(),
                Reminder.priority.desc(),
                Reminder.created_at.asc(),
            )
            .all()
        )

        if not reminders:
            await update.message.reply_text("✅ No active or snoozed reminders right now.")
            return

        from .utils import get_max_active, get_active_count
        slot_line = f"_{get_active_count()} / {get_max_active()} active slots used_\n"

        lines = [f"📋 *Active Reminders*\n{slot_line}"]
        ids: list[int] = []

        for i, r in enumerate(reminders, 1):
            overdue_flag = " ⚠️ OVERDUE" if r.overdue else ""
            if r.status == "snoozed" and r.snooze_until:
                status_flag = f" 💤 until {_fmt_dt(r.snooze_until)}"
            else:
                status_flag = ""
            due_str = f" — Due: {_fmt_date(r.due_date)}" if r.due_date else ""
            lines.append(f"{i}. *{r.title}* [P{r.priority}]{overdue_flag}{status_flag}{due_str}")
            ids.append(r.id)

        set_last_list(chat_id, ids)
        lines.append("\n_Reply `1 done`, `1 snooze`, or `1 snooze 4.5`_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _cmd_add(update: Update, flask_app, text: str) -> None:
    title, priority, due_date, remind_at = _parse_add(text)

    if not title:
        await update.message.reply_text(
            "Usage: `add [task title] [p:1-5] [due:today|tomorrow] [remind:HH:MM]`\n"
            "Example: `add Call dentist p:5 due:tomorrow remind:09:00`",
            parse_mode="Markdown",
        )
        return

    with flask_app.app_context():
        from .models import db, Reminder
        from .utils import now_utc, get_active_count, get_max_active, promote_inactive_tasks

        now = now_utc()
        status = "active" if get_active_count() < get_max_active() else "inactive"

        reminder = Reminder(
            title=title,
            priority=priority,
            active_start_hour=now,
            active_end_hour=due_date,   # cross-fill: end = due if only one provided
            due_date=due_date,
            remind_at=remind_at,
            source="telegram",
            status=status,
            created_at=now,
        )
        db.session.add(reminder)
        db.session.commit()

    if status == "inactive":
        reply = f"📥 *{title}* saved as Inactive (active slots full — will promote automatically)."
    else:
        parts = [f"✅ Added: *{title}* [P{priority}]"]
        if due_date:
            parts.append(f"Due: {_fmt_date(due_date)}")
        if remind_at:
            parts.append(f"Remind at: {_fmt_dt(remind_at)}")
        reply = "\n".join(parts)

    await update.message.reply_text(reply, parse_mode="Markdown")


async def _cmd_done(update: Update, flask_app, lower: str) -> None:
    chat_id = update.effective_chat.id
    n = int(lower.split()[0]) - 1  # convert to 0-indexed
    ids = get_last_list(chat_id)

    if not ids:
        await update.message.reply_text(
            "No recent list found. Type `list` first, then use the number.",
            parse_mode="Markdown",
        )
        return
    if n < 0 or n >= len(ids):
        await update.message.reply_text(
            f"⚠️ #{n + 1} is out of range. Type `list` to see current reminders.",
            parse_mode="Markdown",
        )
        return

    reminder_id = ids[n]
    with flask_app.app_context():
        from .models import db, Reminder
        from .utils import promote_inactive_tasks

        reminder = Reminder.query.filter_by(id=reminder_id, deleted_at=None).first()
        if not reminder:
            await update.message.reply_text("⚠️ Reminder not found — it may have been deleted.")
            return
        if reminder.status == "done":
            await update.message.reply_text(
                f"*{reminder.title}* is already done.", parse_mode="Markdown"
            )
            return

        was_occupying_slot = reminder.status in ("active", "snoozed")
        title = reminder.title
        reminder.status = "done"
        db.session.commit()

        if was_occupying_slot:
            promote_inactive_tasks(db)

    await update.message.reply_text(f"✅ Done: *{title}*", parse_mode="Markdown")


async def _cmd_snooze(update: Update, flask_app, lower: str) -> None:
    chat_id = update.effective_chat.id
    parts = lower.split()
    n = int(parts[0]) - 1  # 0-indexed
    hours = float(parts[2]) if len(parts) >= 3 else 1.0
    ids = get_last_list(chat_id)

    if not ids:
        await update.message.reply_text(
            "No recent list found. Type `list` first, then use the number.",
            parse_mode="Markdown",
        )
        return
    if n < 0 or n >= len(ids):
        await update.message.reply_text(
            f"⚠️ #{n + 1} is out of range. Type `list` to see current reminders.",
            parse_mode="Markdown",
        )
        return

    reminder_id = ids[n]
    with flask_app.app_context():
        from .models import db, Reminder
        from .utils import now_utc

        reminder = Reminder.query.filter_by(id=reminder_id, deleted_at=None).first()
        if not reminder:
            await update.message.reply_text("⚠️ Reminder not found — it may have been deleted.")
            return
        if reminder.status not in ("active", "snoozed"):
            await update.message.reply_text(
                f"⚠️ Can't snooze *{reminder.title}* — current status: `{reminder.status}`",
                parse_mode="Markdown",
            )
            return

        title = reminder.title
        reminder.status = "snoozed"
        reminder.snooze_until = now_utc() + timedelta(hours=hours)
        wake_str = _fmt_dt(reminder.snooze_until)
        db.session.commit()

    h_label = f"{hours:g}h"  # '1h', '4.5h', etc. — strips trailing zeros
    await update.message.reply_text(
        f"💤 Snoozed *{title}* for {h_label} — wakes at {wake_str}",
        parse_mode="Markdown",
    )


# ── Parsing helpers ────────────────────────────────────────────────────────

def _parse_add(text: str) -> tuple[str, int, Optional[datetime], Optional[datetime]]:
    """
    Parse 'add [title] [p:N] [due:today|tomorrow] [remind:HH:MM]'.
    Returns (title, priority, due_date_utc_naive, remind_at_utc_naive).
    remind:HH:MM uses 24-hour NY time; if the time has already passed today,
    it is set to the same time tomorrow.
    """
    # Strip the 'add' keyword
    rest = text[3:].strip() if text.lower().startswith("add") else text.strip()

    priority = 3
    due_date: Optional[datetime] = None
    remind_at: Optional[datetime] = None

    # Extract p:N (priority)
    p_match = re.search(r"\bp:([1-5])\b", rest, re.IGNORECASE)
    if p_match:
        priority = int(p_match.group(1))
        rest = re.sub(r"\bp:[1-5]\b", "", rest, flags=re.IGNORECASE)

    # Extract due:today|tomorrow
    due_match = re.search(r"\bdue:(today|tomorrow)\b", rest, re.IGNORECASE)
    if due_match:
        word = due_match.group(1).lower()
        now_ny = datetime.now(NY_TZ)
        if word == "today":
            target_ny = now_ny.replace(hour=23, minute=59, second=0, microsecond=0)
        else:
            target_ny = (now_ny + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)
        due_date = target_ny.astimezone(pytz.utc).replace(tzinfo=None)
        rest = re.sub(r"\bdue:(today|tomorrow)\b", "", rest, flags=re.IGNORECASE)

    # Extract remind:HH:MM (24-hour NY time)
    remind_match = re.search(r"\bremind:(\d{1,2}):(\d{2})\b", rest, re.IGNORECASE)
    if remind_match:
        h, m = int(remind_match.group(1)), int(remind_match.group(2))
        now_ny = datetime.now(NY_TZ)
        target_ny = now_ny.replace(hour=h, minute=m, second=0, microsecond=0)
        if target_ny <= now_ny:
            target_ny += timedelta(days=1)  # already passed today → use tomorrow
        remind_at = target_ny.astimezone(pytz.utc).replace(tzinfo=None)
        rest = re.sub(r"\bremind:\d{1,2}:\d{2}\b", "", rest, flags=re.IGNORECASE)

    title = " ".join(rest.split())  # collapse extra whitespace
    return title, priority, due_date, remind_at


# ── Date formatting helpers ────────────────────────────────────────────────

def _fmt_date(dt: Optional[datetime]) -> str:
    """Format a naive UTC datetime as a short date in NY time. Cross-platform."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    dt_ny = dt.astimezone(NY_TZ)
    # Cross-platform zero-stripping (%-d fails on Windows)
    return dt_ny.strftime("%b %d").replace(" 0", " ")


def _fmt_dt(dt: Optional[datetime]) -> str:
    """Format a naive UTC datetime as date + time in NY time."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    dt_ny = dt.astimezone(NY_TZ)
    day = dt_ny.strftime("%b %d").replace(" 0", " ")
    time_str = dt_ny.strftime("%I:%M %p").lstrip("0")
    return f"{day} {time_str}"

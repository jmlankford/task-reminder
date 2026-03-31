"""
Thermal receipt printer — Part 5.

Prints a formatted list of active reminders to a network thermal printer
at configurable times (default 7:30 AM and 7:30 PM NY time).

Environment variables
---------------------
RECEIPT_PRINTER_IP      IP address of the thermal printer (required to enable)
RECEIPT_PRINTER_PORT    TCP port (default: 9100)
RECEIPT_MORNING_TIME    HH:MM for morning print in NY time (default: 07:30)
RECEIPT_EVENING_TIME    HH:MM for evening print in NY time (default: 19:30)
RECEIPT_FOOTER_TEXT     Custom footer tagline (default: Task Reminder)
"""

import logging
import textwrap
import time
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)

NY_TZ = pytz.timezone("America/New_York")

# Conservative line width for 80mm paper at Font A.
# Works on any ESC/POS-compatible thermal printer.
LINE_WIDTH = 42


# ── formatting helpers ────────────────────────────────────────────────────────

def _cfg(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)


def _stars(priority: int) -> str:
    """
    5-char block-fill priority bar.
    Uses cp437 block elements so they render on any thermal printer:
      █ = FULL BLOCK (cp437 219)  ░ = LIGHT SHADE (cp437 176)
    """
    priority = max(1, min(5, priority))
    return "\u2588" * priority + "\u2591" * (5 - priority)


def _fmt_date(dt: datetime | None) -> str | None:
    """Format a naive-UTC datetime as 'Mon Mar 3' in NY time."""
    if dt is None:
        return None
    ny_dt = pytz.utc.localize(dt).astimezone(NY_TZ)
    # Cross-platform: strftime %d gives zero-padded day; strip with replace
    return ny_dt.strftime("%a %b %d").replace(" 0", " ").replace("  ", " ").strip()


def _divider(char: str = "\u2550") -> str:
    """Full-width divider line (no newline)."""
    return char * LINE_WIDTH


# ── receipt builder ───────────────────────────────────────────────────────────

# Default printer state merged into every line's state dict.
# Passing all keys to p.set() each time prevents any state bleed.
_BASE = {
    "align":  "left",
    "bold":   False,
    "width":  1,
    "height": 1,
    "invert": False,
}


def _build_lines(reminders: list, is_morning: bool) -> list[tuple[str, dict]]:
    """
    Return a list of (text, state_dict) pairs.
    state_dict is passed to printer.set() before printing text.
    """
    now_ny      = pytz.utc.localize(datetime.utcnow()).astimezone(NY_TZ)
    greeting    = "Good Morning" if is_morning else "Good Evening"
    date_long   = now_ny.strftime("%A, %B %d, %Y").replace(" 0", " ").replace("  ", " ").strip()
    time_str    = now_ny.strftime("%I:%M %p").lstrip("0")
    date_short  = now_ny.strftime("%a %b %d").replace(" 0", " ").replace("  ", " ").strip()
    footer_text = _cfg("RECEIPT_FOOTER_TEXT", "Task Reminder")

    # \xb7 = MIDDLE DOT (cp437 183) — safe separator in footer
    footer_stamp = f"Printed: {time_str}  \xb7  {date_short}"

    lines: list[tuple[str, dict]] = []

    def add(text: str, **kwargs) -> None:
        lines.append((text, {**_BASE, **kwargs}))

    # ── Header ────────────────────────────────────────────────────────────────
    add("\n")
    add("TASK REMINDER\n", align="center", bold=True, width=2, height=2)
    add(_divider("\u2550") + "\n")          # ════...════
    add(greeting + "\n",   align="center", bold=True)
    add(date_long + "\n",  align="center")
    add(_divider("\u2550") + "\n")
    add("\n")

    # ── Body ──────────────────────────────────────────────────────────────────
    if not reminders:
        add("** ALL CLEAR **\n",      align="center", bold=True, width=2, height=2)
        add("\n",                      width=1, height=1)
        add("No active reminders.\n", align="center")
        add("\n")
    else:
        for i, r in enumerate(reminders, 1):
            # Overdue banner — inverted black block with white text
            if r.overdue:
                add("***OVERDUE***\n", align="center", bold=True, invert=True)

            # Item line: "NN. <title padded to fill> <blocks>"
            # prefix = " N. " = 4 chars (right-justified index in 2 chars + ". ")
            prefix   = f"{i:>2}. "
            blocks   = _stars(r.priority)   # 5 chars
            sep      = " "                  # 1 char between title and blocks
            title_w  = LINE_WIDTH - len(prefix) - len(sep) - len(blocks)
            title    = r.title
            if len(title) > title_w:
                title = title[: title_w - 3] + "..."
            item_line = f"{prefix}{title:<{title_w}}{sep}{blocks}\n"
            add(item_line, bold=bool(r.overdue), invert=False)

            # Due date
            due = _fmt_date(r.due_date)
            if due:
                add(f"    Due: {due}\n")

            # Notes snippet — first 100 chars, wrapped at indent 4
            if r.notes_details:
                snippet = r.notes_details[:100]
                for chunk in textwrap.wrap(snippet, LINE_WIDTH - 4):
                    add(f"    {chunk}\n")

            add("\n")

    # ── Footer ────────────────────────────────────────────────────────────────
    add(_divider("\u2500") + "\n")          # ────...────
    add(footer_stamp + "\n", align="center")
    add(footer_text  + "\n", align="center")
    add(_divider("\u2550") + "\n")          # ════...════
    add("\n\n\n")                           # paper feed before cut

    return lines


# ── printer I/O ───────────────────────────────────────────────────────────────

def _attempt_print(lines: list[tuple[str, dict]]) -> None:
    """Open a socket to the printer, send all lines, cut paper."""
    from escpos.printer import Network

    host = _cfg("RECEIPT_PRINTER_IP")
    port = int(_cfg("RECEIPT_PRINTER_PORT", "9100"))

    with Network(host, port) as p:
        prev_state: dict | None = None
        for text, state in lines:
            if state != prev_state:
                p.set(**state)
                prev_state = state
            p.text(text)
        p.cut()


def _notify_telegram_error(err: Exception) -> None:
    """Send a Telegram alert about the printer failure, if bot is configured."""
    chat_id_str = _cfg("TELEGRAM_CHAT_ID")
    if not chat_id_str:
        return
    try:
        from .telegram_bot import send_message_sync
        send_message_sync(
            int(chat_id_str),
            (
                "\U0001f5a8 *Receipt Printer Error*\n\n"
                "Could not print receipt after 2 attempts.\n"
                f"Error: {type(err).__name__}: {err}\n\n"
                "Check RECEIPT\\_PRINTER\\_IP and printer connectivity."
            ),
        )
    except Exception:
        logger.exception("Could not send Telegram error notification for receipt printer")


def _do_print(is_morning: bool) -> None:
    """Core print logic. Must be called inside a Flask app context."""
    printer_ip = _cfg("RECEIPT_PRINTER_IP")
    if not printer_ip:
        logger.info("RECEIPT_PRINTER_IP not configured — receipt printing skipped.")
        return

    from .models import Reminder

    reminders = (
        Reminder.query
        .filter(Reminder.status == "active", Reminder.deleted_at.is_(None))
        .order_by(Reminder.priority.desc(), Reminder.created_at.asc())
        .all()
    )

    lines = _build_lines(reminders, is_morning)

    label = "morning" if is_morning else "evening"
    try:
        _attempt_print(lines)
        logger.info("Receipt printed successfully (%s run).", label)
    except Exception as first_err:
        logger.warning(
            "Receipt print attempt 1 failed (%s run): %s — retrying in 15s.",
            label, first_err,
        )
        time.sleep(15)
        try:
            _attempt_print(lines)
            logger.info("Receipt printed on retry (%s run).", label)
        except Exception as second_err:
            logger.error(
                "Receipt print failed after retry (%s run): %s", label, second_err
            )
            _notify_telegram_error(second_err)


# ── public entry point ────────────────────────────────────────────────────────

def run_print(flask_app=None, is_morning: bool | None = None) -> None:
    """
    Trigger a receipt print.

    flask_app  — pass from scheduler jobs (need their own app context);
                 omit when calling from within a Flask request handler.
    is_morning — True = morning greeting, False = evening greeting,
                 None = auto-detect from current NY time (hour < 12).
    """
    if is_morning is None:
        now_ny = pytz.utc.localize(datetime.utcnow()).astimezone(NY_TZ)
        is_morning = now_ny.hour < 12

    if flask_app is not None:
        with flask_app.app_context():
            _do_print(is_morning)
    else:
        _do_print(is_morning)

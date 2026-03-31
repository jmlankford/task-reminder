"""
Task Reminder — Windows System Tray Client  (Part 6)

Runs silently in the system tray and polls the Flask server every 60 seconds.

Notification rules
------------------
  • At 7:30 AM and 7:30 PM (configurable): toast every active reminder that has
    no specific remind_at time set.
  • At any configured remind_at time: toast that specific reminder.
  • Each (reminder, slot) pair only fires once per app session — no spam.
  • Overdue reminders are flagged in the toast title.

Tray menu
---------
  Poll Now | Open Web UI | ── | Exit

Setup
-----
  1.  pip install -r requirements_tray.txt
  2.  Edit config.ini with your server URL and preferred times.
  3.  To auto-start at Windows login — two options:
        A) Press Win+R → shell:startup → drag a shortcut to start_tray.bat there.
        B) Press Win+R → regedit →
             HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
           Add a String value:  TaskReminder  →  C:\\...\\start_tray.bat

Note on click-to-open
---------------------
  plyer's Windows backend does not expose click callbacks on toast notifications.
  Use the "Open Web UI" tray menu item as the alternative.
  If you want native click-to-open, swap plyer for winotify (pip install winotify)
  and replace _fire_toast() accordingly — see the commented example at the bottom.
"""

import configparser
import datetime
import logging
import sys
import threading
import webbrowser
from pathlib import Path

import pytz

# ── Paths ──────────────────────────────────────────────────────────────────────
HERE = Path(__file__).parent.resolve()
CONFIG_PATH = HERE / "config.ini"
LOG_PATH    = HERE / "tray.log"

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        # Also print to stdout so pythonw suppresses it cleanly
    ],
)
logger = logging.getLogger(__name__)


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    return cfg


def _get(cfg: configparser.ConfigParser, key: str, fallback: str) -> str:
    return cfg.get("taskreminder", key, fallback=fallback)


# ── Notification state ─────────────────────────────────────────────────────────
# Each entry is a (reminder_id, slot_key) tuple.
# slot_key examples:
#   "morning_2026-03-29"          scheduled morning run
#   "evening_2026-03-29"          scheduled evening run
#   "remind_2026-03-29T14:30"     specific remind_at hit
#
# The set resets on app restart — intentional, so you get your 7:30 AM toast
# even if the app was restarted between sessions.

_notified: set[tuple[int, str]] = set()
_state_lock = threading.Lock()


# ── Tray icon image ────────────────────────────────────────────────────────────

def _make_icon() -> "Image.Image":
    """Generate a simple 64×64 bell-shaped tray icon using Pillow."""
    from PIL import Image, ImageDraw

    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    navy   = (30,  40,  60,  255)   # dark background circle
    gold   = (255, 200,  50,  255)  # bell colour (matches app accent)

    draw.ellipse([0, 0, 63, 63], fill=navy)           # background
    draw.ellipse([16, 14, 48, 44], fill=gold)          # bell body
    draw.rectangle([14, 38, 50, 46], fill=gold)        # bell skirt
    draw.rectangle([24, 46, 40, 52], fill=gold)        # base bar
    draw.ellipse([28, 50, 36, 58],   fill=gold)        # clapper
    draw.rectangle([29,  4, 35, 16], fill=gold)        # handle/stem

    return img


# ── Toast ──────────────────────────────────────────────────────────────────────

def _fire_toast(title: str, body: str) -> None:
    """Fire a Windows desktop notification via plyer."""
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=body,
            app_name="Task Reminder",
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Toast failed: %s", exc)


# ── winotify drop-in (click-to-open support) ──────────────────────────────────
# Uncomment this and comment out the plyer version above if you want clicking
# the toast to open the web UI directly.  Requires:  pip install winotify
#
# def _fire_toast(title: str, body: str, launch_url: str = "") -> None:
#     from winotify import Notification
#     toast = Notification(
#         app_id="Task Reminder",
#         title=title,
#         msg=body,
#         duration="short",
#         launch=launch_url,
#     )
#     toast.show()


# ── API fetch ──────────────────────────────────────────────────────────────────

def _fetch_active(base_url: str) -> list[dict] | None:
    """
    GET /reminders — returns active reminders only.
    Returns None on any network or HTTP error (caller skips the cycle).
    """
    import urllib.error
    import urllib.request
    import json

    url = base_url.rstrip("/") + "/reminders"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read())
        return payload.get("data", []) if payload.get("success") else []
    except urllib.error.URLError as exc:
        logger.debug("Server unreachable: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected fetch error: %s", exc)
        return None


# ── Slot detection ─────────────────────────────────────────────────────────────

def _active_slots(
    now_local: datetime.datetime,
    morning_hm: str,
    evening_hm: str,
) -> list[tuple[str, str]]:
    """
    Return a list of (slot_key, label) for any scheduled window that is firing
    right now (exact HH:MM match in local time).
    """
    hm_now   = now_local.strftime("%H:%M")
    date_str = now_local.strftime("%Y-%m-%d")
    slots    = []
    if hm_now == morning_hm:
        slots.append((f"morning_{date_str}", "Good Morning"))
    if hm_now == evening_hm:
        slots.append((f"evening_{date_str}", "Good Evening"))
    return slots


# ── Core poll ──────────────────────────────────────────────────────────────────

def _poll(cfg: configparser.ConfigParser) -> None:
    base_url     = _get(cfg, "flask_url",     "http://localhost:5000")
    tz_name      = _get(cfg, "timezone",      "America/New_York")
    morning_hm   = _get(cfg, "morning_time",  "07:30")
    evening_hm   = _get(cfg, "evening_time",  "19:30")

    tz        = pytz.timezone(tz_name)
    now_utc   = datetime.datetime.utcnow()
    now_local = pytz.utc.localize(now_utc).astimezone(tz)

    reminders = _fetch_active(base_url)
    if reminders is None:
        return   # server down — skip quietly, try again next tick

    if not reminders:
        return

    slots = _active_slots(now_local, morning_hm, evening_hm)

    with _state_lock:
        # ── 1. Scheduled 7:30 AM / 7:30 PM runs ───────────────────────────────
        for slot_key, _label in slots:
            # Only reminders without a specific remind_at go here
            targets = [r for r in reminders if not r.get("remind_at")]
            for r in targets:
                key = (r["id"], slot_key)
                if key in _notified:
                    continue
                overdue = "⚠ OVERDUE — " if r.get("overdue") else ""
                _fire_toast(
                    title=f"🔔 {overdue}{r['title']}",
                    body="Reply done or snooze in Telegram",
                )
                _notified.add(key)
                logger.info("Toast [%s] → %s", slot_key, r["title"])

        # ── 2. remind_at specific times ────────────────────────────────────────
        now_hm_local   = now_local.strftime("%H:%M")
        now_date_local = now_local.strftime("%Y-%m-%d")

        for r in reminders:
            remind_raw = r.get("remind_at")
            if not remind_raw:
                continue

            try:
                remind_utc   = datetime.datetime.fromisoformat(remind_raw)
                remind_local = pytz.utc.localize(remind_utc).astimezone(tz)
            except ValueError:
                continue

            remind_hm   = remind_local.strftime("%H:%M")
            remind_date = remind_local.strftime("%Y-%m-%d")

            if remind_hm != now_hm_local or remind_date != now_date_local:
                continue   # not due yet (or already passed)

            slot_key = f"remind_{remind_date}T{remind_hm}"
            key      = (r["id"], slot_key)
            if key in _notified:
                continue

            overdue = "⚠ OVERDUE — " if r.get("overdue") else ""
            _fire_toast(
                title=f"🔔 {overdue}{r['title']}",
                body="Reply done or snooze in Telegram",
            )
            _notified.add(key)
            logger.info("Toast [remind_at %s] → %s", slot_key, r["title"])


# ── Polling thread ─────────────────────────────────────────────────────────────

def _poll_loop(stop: threading.Event) -> None:
    """Background thread: poll every 60 s, re-reading config each cycle."""
    while not stop.is_set():
        try:
            _poll(_load_config())
        except Exception:
            logger.exception("Unhandled error in poll cycle")
        stop.wait(60)
    logger.info("Poll loop exited.")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    if sys.platform != "win32":
        print("This tray client is for Windows only.")
        sys.exit(1)

    import pystray

    if not CONFIG_PATH.exists():
        print(f"config.ini not found at {CONFIG_PATH}")
        sys.exit(1)

    logger.info("Task Reminder tray starting.")

    stop_event = threading.Event()

    # Start background poller
    poll_thread = threading.Thread(
        target=_poll_loop,
        args=(stop_event,),
        daemon=True,
        name="poller",
    )
    poll_thread.start()

    # ── Tray menu callbacks ────────────────────────────────────────────────────

    def on_poll_now(icon, item):
        logger.info("Manual poll triggered from tray.")
        try:
            _poll(_load_config())
        except Exception:
            logger.exception("Error during manual poll")

    def on_open_webui(icon, item):
        url = _get(_load_config(), "flask_url", "http://localhost:5000")
        webbrowser.open(url)

    def on_exit(icon, item):
        stop_event.set()
        icon.stop()

    # ── Create and run the tray icon ───────────────────────────────────────────
    icon = pystray.Icon(
        name="task_reminder",
        icon=_make_icon(),
        title="Task Reminder",
        menu=pystray.Menu(
            pystray.MenuItem("Poll Now",    on_poll_now),
            pystray.MenuItem("Open Web UI", on_open_webui),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit",        on_exit),
        ),
    )

    icon.run()   # blocks the main thread until on_exit() fires


if __name__ == "__main__":
    main()

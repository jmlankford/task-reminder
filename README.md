# Task Reminder

A self-hosted homelab reminder system built with Python Flask. Manage a prioritized list of active reminders through a web UI, Telegram bot, and Windows tray client — with Google Calendar sync, thermal receipt printing, and Home Assistant integration.

---

## Features

| Part | Feature |
|------|---------|
| 1 | Flask/SQLite REST API — status lifecycle, priority cap, soft delete |
| 2 | Dark-theme web UI — card + list views, filters, settings |
| 3 | Telegram bot — numbered commands, HA webhook notify |
| 4 | Google Calendar sync — pull `[reminder]`-tagged events every 30 min |
| 5 | Thermal receipt printer — ESC/POS, 80mm, scheduled AM/PM |
| 6 | Windows tray client — 60s poll, toast notifications |
| 7 | Home Assistant — presence check, light pulse, Alexa announce |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Flask App (Docker)                  │
│                                                      │
│  REST API  ·  APScheduler  ·  SQLite                 │
│                                                      │
│  /reminders    /config      /gcal/sync               │
│  /ha/*         /receipt/print  /telegram/notify      │
└────────┬──────────────┬───────────────┬──────────────┘
         │              │               │
    ┌────▼────┐   ┌─────▼─────┐  ┌─────▼──────┐
    │  Web UI │   │  Telegram  │  │ Google Cal │
    │ browser │   │    Bot     │  │   Sync     │
    └─────────┘   └───────────┘  └────────────┘
         │
    ┌────▼──────────────────────────────────────┐
    │            Home Assistant                  │
    │  Presence check → Flask trigger-check      │
    │  Flask → HA webhook → light pulse / Alexa  │
    └───────────────────────────────────────────┘
         │
    ┌────▼──────────────┐     ┌──────────────────┐
    │  Thermal Printer  │     │  Windows Tray    │
    │  (ESC/POS 80mm)   │     │  (toast notify)  │
    └───────────────────┘     └──────────────────┘
```

---

## Quick Start (Portainer / Docker)

### 1. Pull the image directly from GitHub Container Registry

In Portainer: **Stacks → Add Stack → Web editor**, paste the compose below and fill in your environment variables.

```yaml
services:
  taskreminder:
    image: ghcr.io/jmlankford/task-reminder:latest
    container_name: taskreminder
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - /mnt/user/appdata/taskreminder:/data
    environment:
      - DATABASE_PATH=/data/taskreminder.db
      - TZ=America/New_York
      - TELEGRAM_BOT_TOKEN=your-token
      - TELEGRAM_CHAT_ID=your-chat-id
      - TELEGRAM_ALLOWED_IDS=your-user-id
      - TELEGRAM_NOTIFY_API_KEY=a-long-random-string
      # Add more vars as needed — see Configuration below
```

### 2. First run

The database is created automatically at `/data/taskreminder.db` on first start. No migrations to run manually.

### 3. Access the web UI

```
http://YOUR_SERVER_IP:5000
```

---

## Deploying Updates

The image rebuilds automatically on every push to `main` via GitHub Actions and publishes to `ghcr.io/jmlankford/task-reminder:latest`.

In Portainer: **Stacks → task-reminder → Pull and redeploy**

---

## Configuration

All configuration is via environment variables. Copy `.env.example` as a reference.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `/data/taskreminder.db` | SQLite database path inside the container |
| `TZ` | `America/New_York` | Container timezone |

### Telegram Bot

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Yes | Your personal chat ID (find with [@userinfobot](https://t.me/userinfobot)) |
| `TELEGRAM_ALLOWED_IDS` | Recommended | Comma-separated Telegram user IDs allowed to use the bot |
| `TELEGRAM_NOTIFY_API_KEY` | Recommended | API key for the HA → Flask webhook (`/telegram/notify`) |
| `TELEGRAM_NOTIFY_COOLDOWN_HOURS` | `4` | Hours before re-notifying about the same reminder |

### Google Calendar Sync

| Variable | Default | Description |
|----------|---------|-------------|
| `GCAL_SERVICE_ACCOUNT_JSON` | `/data/gcal_service_account.json` | Path to your service account key file |
| `GCAL_CALENDAR_IDS` | `primary` | Comma-separated calendar IDs to watch |
| `GCAL_LOOKAHEAD_HOURS` | `24` | Hours ahead to fetch events |
| `GCAL_API_KEY` | *(none)* | Optional key to protect `POST /gcal/sync` |

**Setup:** Create a Google Cloud service account with Calendar read-only scope, download the JSON key, place it at the path above, and share your calendar with the service account's `client_email`. Tag any event with `[reminder]` in the title or description to sync it.

### Thermal Receipt Printer

| Variable | Default | Description |
|----------|---------|-------------|
| `RECEIPT_PRINTER_IP` | *(none — disables printing)* | IP of your ESC/POS network printer |
| `RECEIPT_PRINTER_PORT` | `9100` | TCP port |
| `RECEIPT_MORNING_TIME` | `07:30` | Scheduled print time (NY time, 24h HH:MM) |
| `RECEIPT_EVENING_TIME` | `19:30` | Scheduled print time |
| `RECEIPT_FOOTER_TEXT` | `Task Reminder` | Custom tagline on every receipt |

### Home Assistant

| Variable | Default | Description |
|----------|---------|-------------|
| `HA_API_KEY` | *(none)* | Shared secret — must match `task_reminder_ha_api_key` in HA `secrets.yaml` |
| `HA_WEBHOOK_BASE_URL` | *(none)* | e.g. `http://192.168.1.165:8123` |
| `HA_LIGHT_ON_WEBHOOK_ID` | `task-reminder-light-on` | HA webhook ID for light on |
| `HA_LIGHT_OFF_WEBHOOK_ID` | `task-reminder-light-off` | HA webhook ID for light off |
| `HA_ANNOUNCE_WEBHOOK_ID` | `task-reminder-announce` | HA webhook ID for Alexa announce |

---

## Reminder Status Lifecycle

```
inactive ──► active ──► done
    ▲           │
    │        snoozed
    │           │
scheduled ──────┘
    │
inactive_passed  (active window expired)
```

- **active** — visible, counts against the cap (default: 5)
- **inactive** — waiting for a slot; promoted automatically when one opens
- **scheduled** — has a future `active_start_hour`; activates when that time arrives (bypasses cap)
- **snoozed** — hidden until `snooze_until` lapses, then returns to active
- **done** — completed; never promoted again
- **inactive_passed** — active window expired before the reminder was acted on

---

## API Reference

All endpoints return `{"success": true, "data": ...}` or `{"success": false, "error": "..."}`.

### Reminders

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/reminders` | Active reminders only, sorted by priority |
| `GET` | `/reminders/all` | All reminders including soft-deleted |
| `POST` | `/reminders` | Create a reminder |
| `PATCH` | `/reminders/<id>` | Update any field |
| `DELETE` | `/reminders/<id>` | Soft-delete |
| `POST` | `/reminders/<id>/done` | Mark done |
| `POST` | `/reminders/<id>/snooze?hours=N` | Snooze for N hours |
| `POST` | `/reminders/<id>/activate` | Force-activate |

**POST /reminders body:**
```json
{
  "title": "Buy milk",
  "priority": 3,
  "active_start_hour": "2026-03-29T07:30",
  "active_end_hour":   "2026-03-29T19:30",
  "due_date":          "2026-03-29T23:59",
  "remind_at":         "2026-03-29T14:00",
  "notes_details":     "Optional notes",
  "source":            "manual"
}
```

Priority runs 1 (lowest) → 5 (highest). All datetime fields are optional and accept ISO 8601 strings.

### Config

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/config` | Get current settings |
| `PUT` | `/config` | Update settings (e.g. `{"max_active": 8}`) |

### Integrations

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/telegram/notify` | `X-API-Key` | HA webhook → send Telegram reminder summary |
| `POST` | `/gcal/sync` | `X-API-Key` | Manually trigger Google Calendar sync |
| `POST` | `/receipt/print?time=morning\|evening` | — | Manually trigger a receipt print |
| `POST` | `/ha/trigger-check` | `X-API-Key` | HA presence check → Telegram summary |
| `POST` | `/ha/light-on` | — | Tell HA to start amber light pulse |
| `POST` | `/ha/light-off` | — | Tell HA to stop pulse and turn off light |
| `GET\|POST` | `/ha/announce?message=…` | — | Tell HA to announce via Alexa |

---

## Telegram Bot Commands

Send these to your bot (with or without a leading `/`):

| Command | Description |
|---------|-------------|
| `list` | Numbered list of active + snoozed reminders |
| `add Buy milk` | Add a reminder (default priority 3) |
| `add Buy milk p:5` | Add with priority 1–5 |
| `add Buy milk due:today` | Due tonight 11:59 PM |
| `add Buy milk due:tomorrow` | Due tomorrow 11:59 PM |
| `add Buy milk remind:14:30` | Toast/print notification at 2:30 PM |
| `1 done` | Mark reminder #1 as done |
| `1 snooze` | Snooze #1 for 1 hour |
| `1 snooze 4.5` | Snooze for 4.5 hours |
| `help` | Show command list |

Options can be combined: `add Call dentist p:5 due:tomorrow remind:09:00`

Numbers refer to the last `list` output. Run `list` to refresh.

---

## Home Assistant Setup

See `ha/configuration_additions.yaml` for full installation instructions. Summary:

1. Install **HACS Alexa Media Player** and add your Amazon account
2. Add the `rest_command` and `input_boolean` blocks from `configuration_additions.yaml` to your `configuration.yaml` → restart HA
3. Import the script from `ha/scripts.yaml` via **Settings → Scripts**
4. Import the 4 automations from `ha/automations.yaml` via **Settings → Automations**
5. Add to `secrets.yaml`: `task_reminder_ha_api_key: your-shared-key`
6. Set `HA_API_KEY` to the same value in your Docker environment

**HA Webhook URLs** (HA receives these from Flask):
```
POST http://192.168.1.165:8123/api/webhook/task-reminder-light-on
POST http://192.168.1.165:8123/api/webhook/task-reminder-light-off
POST http://192.168.1.165:8123/api/webhook/task-reminder-announce?message=Hello
```

**Flask endpoint** (HA calls this):
```
POST http://YOUR_SERVER_IP:5000/ha/trigger-check
Header: X-API-Key: your-shared-key
```

---

## Windows Tray Client

Located in `tray/`. Runs silently at startup, polls the Flask server every 60 seconds, and fires Windows toast notifications.

### Install

```powershell
cd tray
pip install -r requirements_tray.txt
```

### Configure

Edit `tray/config.ini`:
```ini
[taskreminder]
flask_url    = http://YOUR_SERVER_IP:5000
timezone     = America/New_York
morning_time = 07:30
evening_time = 19:30
```

### Run

```
start_tray.bat       # silent (no console window)
python taskreminder_tray.py  # with console for testing
```

### Auto-start at login

**Method A:** Press `Win+R` → `shell:startup` → drop a shortcut to `start_tray.bat` there.

**Method B:** Press `Win+R` → `regedit` → navigate to:
```
HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
```
Add a String value: `TaskReminder` → full path to `start_tray.bat`

### Notification logic

- **7:30 AM and 7:30 PM:** toasts all active reminders without a `remind_at` set
- **`remind_at` time:** toasts that specific reminder at the configured time
- Each `(reminder, slot)` pair fires once per app session

---

## Project Structure

```
taskreminder/
├── app/
│   ├── models.py            # SQLAlchemy models (Reminder, Config)
│   ├── utils.py             # Timezone helpers, cap logic, promotion
│   ├── scheduler.py         # APScheduler jobs (tick, gcal, receipt)
│   ├── telegram_bot.py      # Telegram polling bot (daemon thread)
│   ├── gcal_sync.py         # Google Calendar sync logic
│   ├── receipt_printer.py   # ESC/POS thermal receipt formatting
│   ├── routes/
│   │   ├── reminders.py     # Core CRUD endpoints
│   │   ├── config.py        # GET/PUT /config
│   │   ├── telegram.py      # POST /telegram/notify (HA webhook)
│   │   ├── gcal.py          # POST /gcal/sync
│   │   ├── receipt.py       # POST /receipt/print
│   │   └── ha.py            # /ha/* endpoints
│   ├── templates/
│   │   └── index.html       # Single-page web UI shell
│   └── static/
│       ├── css/style.css    # Dark/light theme
│       └── js/app.js        # Vanilla JS UI logic
├── ha/
│   ├── automations.yaml          # 4 HA automations
│   ├── scripts.yaml              # Amber pulse script
│   └── configuration_additions.yaml  # rest_command + helper setup guide
├── tray/
│   ├── taskreminder_tray.py  # Windows tray client
│   ├── config.ini            # Tray configuration
│   ├── requirements_tray.txt
│   └── start_tray.bat        # Silent launcher
├── .github/
│   └── workflows/
│       └── docker-publish.yml  # Auto-build + push to GHCR
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── run.py
```

---

## Local Development

```bash
# Clone
git clone https://github.com/jmlankford/task-reminder.git
cd task-reminder

# Install dependencies
pip install -r requirements.txt

# Set env vars (copy and edit)
cp .env.example .env

# Run (dev mode, auto-reloads)
python dev_run.py
```

The web UI is available at `http://localhost:5000`.

---

## License

MIT

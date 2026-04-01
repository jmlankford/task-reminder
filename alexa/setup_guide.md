# Alexa Skill Setup Guide

This guide walks you through connecting your Task Reminder Flask app to a custom
Alexa skill so you can manage tasks entirely by voice.

---

## Overview

```
You (voice) → Alexa → Alexa Developer Cloud → HTTPS → Your Flask app
                                                         ↓
                                                    SQLite DB
                                                         ↓
                                               HA announce webhook (daily summary)
```

Alexa requires a **publicly reachable HTTPS endpoint**. The recommended approach
for a home server is **Cloudflare Tunnel** — free, no port forwarding, automatic
valid TLS certificate.

---

## Step 1 — Expose your Flask app via Cloudflare Tunnel

1. On your Unraid server (or wherever Flask runs), install `cloudflared`:
   ```bash
   # Unraid: install the Cloudflare Tunnel plugin from Community Apps
   # Or manually:
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
       -O /usr/local/bin/cloudflared
   chmod +x /usr/local/bin/cloudflared
   ```

2. Create a quick tunnel (no account needed for testing):
   ```bash
   cloudflared tunnel --url http://localhost:5000
   ```
   It will print a URL like `https://random-words.trycloudflare.com`. Copy it.

3. For a **permanent tunnel** (recommended for production):
   - Sign up at cloudflare.com (free)
   - `cloudflared login`
   - `cloudflared tunnel create task-reminder`
   - Configure a subdomain in your Cloudflare dashboard (e.g. `tasks.yourdomain.com`)
   - Set it to run as a service so it survives reboots

Your Alexa endpoint will be: `https://your-tunnel-url/alexa/webhook`

---

## Step 2 — Create the Alexa Skill

1. Go to **developer.amazon.com/alexa** and sign in with your Amazon account
2. Click **Create Skill**
3. Settings:
   - **Skill name**: Task Reminder (or anything you like)
   - **Primary locale**: English (US)
   - **Model**: Custom
   - **Hosting**: Provision your own
4. Click **Create Skill**, then choose **Start from Scratch**

---

## Step 3 — Upload the Interaction Model

1. In the skill builder, go to **JSON Editor** (left sidebar)
2. Paste the entire contents of `alexa/interaction_model.json`
3. Click **Save Model**, then **Build Model** — wait for it to finish

---

## Step 4 — Set the Endpoint

1. Go to **Endpoint** in the left sidebar
2. Select **HTTPS**
3. Paste your tunnel URL: `https://your-tunnel-url/alexa/webhook`
4. For **SSL certificate type** select:
   - "My development endpoint is a sub-domain of a domain that has a wildcard certificate from a certificate authority" → if using Cloudflare
   - "My development endpoint has a certificate from a trusted certificate authority" → also works for Cloudflare
5. Click **Save Endpoints**

---

## Step 5 — Copy your Skill ID

1. In the Alexa Developer Console, go to **Endpoint** or the skill's main page
2. Copy the **Skill ID** — it looks like: `amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

---

## Step 6 — Update docker-compose.yml

Add these environment variables:

```yaml
environment:
  # ...existing vars...

  # ── Alexa skill ────────────────────────────────────────────────────────────
  - ALEXA_SKILL_ID=amzn1.ask.skill.your-skill-id-here

  # ── Alexa daily summary ────────────────────────────────────────────────────
  # Requires HA_WEBHOOK_BASE_URL and HA_ANNOUNCE_WEBHOOK_ID to be set
  - ALEXA_DAILY_SUMMARY_ENABLED=true
  - ALEXA_DAILY_SUMMARY_TIME=08:00    # HH:MM NY time
```

Then restart:
```bash
docker compose up -d
```

---

## Step 7 — Test the Skill

In the Alexa Developer Console, go to the **Test** tab and set it to
**Development** mode. Try:

- *"Alexa, open task reminder"*
- *"Alexa, ask task reminder to list my tasks"*
- *"Alexa, ask task reminder to add buy milk due tomorrow"*

Or just say it to your Echo device after enabling developer mode on your Alexa app
(the skill appears under Your Skills → Dev).

---

## Voice Command Reference

| What you say | What it does |
|---|---|
| `add [title] due [date]` | Add a new task |
| `add [title] due [date] starting at [time]` | Add with start hour |
| `add [title] due [date] priority [1-5]` | Add with priority |
| `list my tasks` | Read out all active tasks |
| `snooze [title]` | Snooze until tomorrow evening |
| `snooze [title] for [duration]` | Snooze for 2 hours, 1 day, etc. |
| `snooze [title] until [date]` | Snooze until evening on that date |
| `snooze [title] until [time]` | Snooze until next occurrence of that time |
| `remind me at [time] to [title]` | Set a specific remind_at time |
| `remind me at [time] to task [number]` | Set remind_at by task number |
| `mark [title] as done` | Mark task completed |
| `delete [title]` | Remove a task |

**All commands that act on a task require a title.** If you omit it, Alexa will ask.
**Add commands require a due date.** If you omit it, Alexa will ask.

Alexa's AI handles natural phrasing — you don't need to say commands word-for-word.
Tasks created via Alexa are tagged with `source: alexa` in the database.

---

## Daily Summary

When `ALEXA_DAILY_SUMMARY_ENABLED=true` is set, the Flask scheduler will announce
your active task list through your Echo at `ALEXA_DAILY_SUMMARY_TIME` each morning.

This uses the same Home Assistant announce webhook from Part 7. Make sure:
- `HA_WEBHOOK_BASE_URL` is set (e.g. `http://192.168.1.165:8123`)
- `HA_ANNOUNCE_WEBHOOK_ID` is set (default: `task-reminder-announce`)
- The `task_reminder_alexa_announce` automation is active in HA

---

## Troubleshooting

**Skill returns "There was a problem with the requested skill's response"**
→ Check `docker logs taskreminder --tail 50` — usually a Python exception

**Alexa says "I couldn't find that task"**
→ Task titles are matched by substring. Try listing tasks first to hear exact names.

**Daily summary not firing**
→ Check `ALEXA_DAILY_SUMMARY_ENABLED=true` and `HA_WEBHOOK_BASE_URL` are both set.
→ Check logs for "Alexa daily summary scheduled at HH:MM"

**Tunnel goes down**
→ For reliability, run `cloudflared` as a service or use Cloudflare's named tunnel
  with a permanent subdomain.

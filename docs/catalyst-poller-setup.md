# Catalyst Poller — Windows Task Scheduler Setup

## 1. Secrets

Copy `.env.example` to `.env` and fill in all seven variables:

| Variable | Description |
|---|---|
| `GMAIL_USER` | Dedicated `alerts@` Gmail address used to send alert emails. |
| `GMAIL_APP_PW` | 16-character Gmail App Password (Google Account → Security → 2-Step Verification → App passwords). Do **not** use your main account password. |
| `ALERT_TO_EMAIL` | The inbox that receives the alert emails (can be the same address or different). |
| `DISCORD_WEBHOOK_URL` | Webhook URL from Discord — Server Settings → Integrations → Webhooks → New Webhook → Copy Webhook URL. |
| `ANTHROPIC_API_KEY` | Anthropic API key from [console.anthropic.com](https://console.anthropic.com) (starts with `sk-ant-`). |
| `MAX_RERANK_CALLS_PER_DAY` | Daily cap on Claude rerank calls (recommended: `200`). |
| `SEC_USER_AGENT` | User-Agent string sent to EDGAR, e.g. `Dealscout/1.0 contact@example.com`. |

---

## 2. Pre-flight

Run both smoke tests from an activated virtualenv before scheduling:

```cmd
python catalyst_poller.py --dry-run
```

Expected: fetches catalysts, scores them, prints results — sends **no** alerts.

```cmd
python catalyst_poller.py --force-alert
```

Expected: fetches catalysts, scores them, fires a real email + Discord alert for the top result.

---

## 3. Schedule

Open an **elevated** Command Prompt (Run as Administrator) and run:

```cmd
schtasks /Create /TN "DealscoutCatalystPoller" /TR "python C:\Users\mwill\OneDrive\Documents\mwilliams2733\Dealscout\catalyst_poller.py" /SC DAILY /ST 07:00 /RL HIGHEST /F
```

Flags:
- `/TN` — task name
- `/TR` — command to run (absolute path required)
- `/SC DAILY /ST 07:00` — runs every day at 07:00
- `/RL HIGHEST` — runs with highest privileges
- `/F` — force-create (overwrites existing task of same name)

---

## 4. Verify

Query the task to confirm it was registered:

```cmd
schtasks /Query /TN "DealscoutCatalystPoller" /V /FO LIST
```

Trigger an immediate run to test end-to-end:

```cmd
schtasks /Run /TN "DealscoutCatalystPoller"
```

---

## 5. Logs

By default, output goes to **Task Scheduler history** (Event Viewer → Applications and Services Logs → Microsoft → Windows → TaskScheduler → Operational).

To redirect stdout/stderr to a file, create a `.bat` wrapper, e.g. `run_catalyst_poller.bat`:

```bat
@echo off
cd /d C:\Users\mwill\OneDrive\Documents\mwilliams2733\Dealscout
python catalyst_poller.py >> logs\catalyst_poller.log 2>&1
```

Then update the scheduled task's `/TR` to point to the `.bat` file instead.

---

## 6. Kill

To remove the scheduled task entirely:

```cmd
schtasks /Delete /TN "DealscoutCatalystPoller" /F
```

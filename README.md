# uptime-monitor

A lightweight automation tool that polls a list of URLs on a schedule, records
every check to SQLite, and sends a Discord webhook alert the moment a site's
status changes (down → alert, back up → recovery notice).

## Features

- YAML-configured target list - name, URL, timeout, expected status code
- Persists every check (status, latency, error) to SQLite for history/uptime %
- Only alerts on **state changes**, not every failed check - no spam
- Discord embed alerts (red for down, green for recovered)
- `--once` flag for cron/Task Scheduler; otherwise loops on its own interval

## Setup

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
# edit config.yaml — add your URLs and (optionally) a Discord webhook URL
python monitor.py --once          # single check, good for cron
python monitor.py                 # run continuously on the configured interval
```

## How it works

Each cycle, every target is requested with `requests`. A check counts as "up"
only if the response status code matches `expected_status`. The result is
written to `checks` (full history) and compared against `last_status` (the
most recent known state per target) - an alert only fires when those two
disagree, so a site that's been down for an hour doesn't ping you every
minute.

## Example alert

```
🔴 My API is down
URL: https://api.example.com/health
Detail: HTTPConnectionPool(...): Max retries exceeded (Connection refused)
```

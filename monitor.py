"""
Uptime Monitor — polls a list of URLs on an interval, tracks status changes
in SQLite, and sends Discord webhook alerts when a site goes down or recovers.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import requests
import yaml

log = logging.getLogger("uptime-monitor")

DB_PATH = Path(__file__).parent / "uptime.db"


@dataclass
class Target:
    name: str
    url: str
    timeout: float = 10.0
    expected_status: int = 200


@dataclass
class CheckResult:
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None


def load_config(path: str) -> tuple[list[Target], str | None, int]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    targets = [
        Target(
            name=item.get("name", item["url"]),
            url=item["url"],
            timeout=float(item.get("timeout", 10.0)),
            expected_status=int(item.get("expected_status", 200)),
        )
        for item in data.get("targets", [])
    ]
    webhook_url = data.get("discord_webhook_url") or None
    interval = int(data.get("interval_seconds", 60))
    return targets, webhook_url, interval


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT NOT NULL,
                url TEXT NOT NULL,
                is_up INTEGER NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                error TEXT,
                checked_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS last_status (
                target TEXT PRIMARY KEY,
                is_up INTEGER NOT NULL
            )
            """
        )
    return conn


def check_target(target: Target) -> CheckResult:
    start = time.perf_counter()
    try:
        resp = requests.get(target.url, timeout=target.timeout)
        latency_ms = (time.perf_counter() - start) * 1000
        ok = resp.status_code == target.expected_status
        return CheckResult(ok=ok, status_code=resp.status_code, latency_ms=latency_ms, error=None)
    except requests.RequestException as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return CheckResult(ok=False, status_code=None, latency_ms=latency_ms, error=str(exc))


def record_check(conn: sqlite3.Connection, target: Target, result: CheckResult) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO checks (target, url, is_up, status_code, latency_ms, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (target.name, target.url, int(result.ok), result.status_code, result.latency_ms, result.error),
        )


def get_previous_status(conn: sqlite3.Connection, target: Target) -> bool | None:
    row = conn.execute(
        "SELECT is_up FROM last_status WHERE target = ?", (target.name,)
    ).fetchone()
    return bool(row["is_up"]) if row else None


def set_status(conn: sqlite3.Connection, target: Target, is_up: bool) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO last_status (target, is_up) VALUES (?, ?)
            ON CONFLICT(target) DO UPDATE SET is_up = excluded.is_up
            """,
            (target.name, int(is_up)),
        )


def send_discord_alert(webhook_url: str, target: Target, result: CheckResult, recovered: bool) -> None:
    if recovered:
        title, color = f"✅ {target.name} is back up", 0x22C55E
    else:
        title, color = f"🔴 {target.name} is down", 0xEF4444

    detail = result.error or f"Unexpected status code: {result.status_code}"
    fields = [{"name": "URL", "value": target.url, "inline": False}]
    if recovered:
        fields.append({"name": "Status", "value": f"HTTP {result.status_code}", "inline": True})
        if result.latency_ms is not None:
            fields.append({"name": "Latency", "value": f"{result.latency_ms:.0f}ms", "inline": True})
    else:
        fields.append({"name": "Detail", "value": detail, "inline": False})

    payload = {
        "embeds": [{"title": title, "color": color, "fields": fields}],
        "allowed_mentions": {"parse": []},
    }
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except requests.RequestException as exc:
        log.error("Failed to send Discord alert: %s", exc)


def run_cycle(conn: sqlite3.Connection, targets: list[Target], webhook_url: str | None) -> None:
    for target in targets:
        result = check_target(target)
        record_check(conn, target, result)
        previous = get_previous_status(conn, target)

        status_word = "UP" if result.ok else "DOWN"
        latency = f"{result.latency_ms:.0f}ms" if result.latency_ms is not None else "n/a"
        log.info("%-20s %-5s status=%s latency=%s", target.name, status_word, result.status_code, latency)

        if previous is not None and previous != result.ok:
            if webhook_url:
                send_discord_alert(webhook_url, target, result, recovered=result.ok)
            log.warning(
                "%s changed state: %s -> %s",
                target.name,
                "UP" if previous else "DOWN",
                status_word,
            )

        set_status(conn, target, result.ok)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll URLs and alert on status changes via Discord.")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML config file")
    parser.add_argument("--once", action="store_true", help="Run a single check cycle and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    targets, webhook_url, interval = load_config(args.config)
    if not targets:
        raise SystemExit("No targets configured - add at least one under `targets:` in the config.")

    conn = init_db()
    log.info("Monitoring %d target(s), interval=%ds", len(targets), interval)

    if args.once:
        run_cycle(conn, targets, webhook_url)
        return

    try:
        while True:
            run_cycle(conn, targets, webhook_url)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()

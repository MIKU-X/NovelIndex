"""Persistent Qidian detail cache and cooldown; independent of the browser driver."""
import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHINA_TZ = timezone(timedelta(hours=8))


def observed_now():
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def observed_timestamp(value):
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CHINA_TZ)
        return dt.timestamp()
    except (TypeError, ValueError):
        return 0


class QidianState:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS details (
                book_id TEXT PRIMARY KEY, payload TEXT NOT NULL, observed REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cooldown (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                until REAL NOT NULL, reason TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, book_id):
        row = self.db.execute("SELECT payload FROM details WHERE book_id = ?", (book_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, book_id, raw):
        observed = observed_timestamp(raw.get("_word_count_observed_at"))
        if not book_id or not raw.get("wordCount") or not observed:
            return
        payload = {key: raw.get(key, "") for key in ("wordCount", "author", "intro", "_word_count_observed_at")}
        previous = self.get(book_id) or {}
        for key in ("author", "intro"):
            payload[key] = payload[key] or previous.get(key, "")
        if payload == previous:
            return
        with self.db:
            self.db.execute("""
                INSERT INTO details VALUES (?, ?, ?)
                ON CONFLICT(book_id) DO UPDATE SET payload=excluded.payload, observed=excluded.observed
                WHERE excluded.observed >= details.observed
            """, (book_id, json.dumps(payload, ensure_ascii=False), observed))

    def defer(self, seconds, reason):
        seconds = max(1, seconds) if math.isfinite(seconds) else 3600
        until = time.time() + seconds
        with self.db:
            self.db.execute("""
                INSERT INTO cooldown VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET until=excluded.until, reason=excluded.reason
                WHERE excluded.until > cooldown.until
            """, (until, reason))

    def remaining_cooldown(self):
        row = self.db.execute("SELECT until, reason FROM cooldown WHERE singleton=1").fetchone()
        if row and row[0] > time.time():
            return math.ceil(row[0] - time.time()), row[1]
        return 0, ""

    def prune(self, max_entries=5000):
        with self.db:
            self.db.execute("DELETE FROM details WHERE observed < ?", (time.time() - 30 * 86400,))
            self.db.execute("""
                DELETE FROM details WHERE book_id NOT IN
                (SELECT book_id FROM details ORDER BY observed DESC LIMIT ?)
            """, (max_entries,))

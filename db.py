"""Local history database (history.db in this folder) for the app tracker and network watcher.

Nothing here leaves your PC. Anything older than KEEP_DAYS is deleted automatically.
"""
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from config import DATA_DIR

DB_FILE = DATA_DIR / "history.db"
KEEP_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_day (
    day TEXT, app TEXT, samples INTEGER, cpu_sum REAL, cpu_peak REAL, cpu_peak_at INTEGER,
    mem_sum REAL, mem_peak REAL, mem_peak_at INTEGER, cpu_seconds REAL,
    PRIMARY KEY (day, app));
CREATE TABLE IF NOT EXISTS app_minute (ts INTEGER, app TEXT, cpu REAL, mem REAL);
CREATE INDEX IF NOT EXISTS app_minute_ts ON app_minute (ts);
CREATE TABLE IF NOT EXISTS tracked_minute (ts INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS net_minute (
    ts INTEGER, target TEXT, sent INTEGER, lost INTEGER, avg_ms REAL, max_ms REAL,
    PRIMARY KEY (ts, target));
CREATE TABLE IF NOT EXISTS wifi_minute (ts INTEGER PRIMARY KEY, signal INTEGER);
CREATE TABLE IF NOT EXISTS net_event (start INTEGER, end INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS speed_test (ts INTEGER, down_mbps REAL, up_mbps REAL, ping_ms REAL);
CREATE TABLE IF NOT EXISTS game_session (
    id INTEGER PRIMARY KEY, name TEXT, start INTEGER, end INTEGER, data BLOB);
CREATE INDEX IF NOT EXISTS game_session_start ON game_session (start);
"""


_setup_lock = threading.Lock()
_set_up_file = None


def connect() -> sqlite3.Connection:
    """Open the database. The first call sets it up; the lock stops the app tracker and the
    network watcher (which start at the same moment) from setting it up at the same time."""
    global _set_up_file
    with _setup_lock:
        con = sqlite3.connect(DB_FILE, timeout=10)
        if _set_up_file != DB_FILE:
            con.execute("PRAGMA journal_mode=WAL")  # lets Reports read while recorders write
            con.executescript(SCHEMA)
            _set_up_file = DB_FILE
    return con


def prune(con: sqlite3.Connection):
    cutoff = int(time.time() - KEEP_DAYS * 86400)
    cutoff_day = time.strftime("%Y-%m-%d", time.localtime(cutoff))
    con.execute("DELETE FROM app_day WHERE day < ?", (cutoff_day,))
    for table in ("app_minute", "tracked_minute", "net_minute", "wifi_minute", "speed_test"):
        con.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
    con.execute("DELETE FROM net_event WHERE start < ?", (cutoff,))
    con.execute("DELETE FROM game_session WHERE start < ?", (cutoff,))
    con.commit()


def day_bounds(day: str) -> tuple[int, int]:
    """Start and end (Unix time) of a local calendar day like "2026-10-21"."""
    start = datetime.strptime(day, "%Y-%m-%d")
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def days_with_data(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("""
        SELECT day FROM app_day
        UNION SELECT date(ts, 'unixepoch', 'localtime') FROM net_minute
        UNION SELECT date(ts, 'unixepoch', 'localtime') FROM speed_test
        UNION SELECT date(start, 'unixepoch', 'localtime') FROM game_session
        ORDER BY 1 DESC""").fetchall()
    return [r[0] for r in rows]

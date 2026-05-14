"""
SQLite persistent store for workout data.
Keeps fetched workouts across sessions so we only fetch new ones from Concept2.
Database lives at data/workouts.db (gitignored).
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DB_PATH = Path(__file__).parent / "data" / "workouts.db"


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workouts (
            user_id  TEXT    NOT NULL,
            id       INTEGER NOT NULL,
            date     TEXT    NOT NULL,
            data     TEXT    NOT NULL,
            PRIMARY KEY (user_id, id)
        );
        CREATE INDEX IF NOT EXISTS idx_workouts_user_date
            ON workouts (user_id, date DESC);
        CREATE TABLE IF NOT EXISTS sync_log (
            user_id     TEXT PRIMARY KEY,
            last_synced TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cache (
            key        TEXT PRIMARY KEY,
            data       TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
    """)
    conn.commit()


def _db() -> sqlite3.Connection:
    conn = _connect()
    _init(conn)
    return conn


def get_all_user_ids() -> list[str]:
    """Return all user IDs with stored workouts, most recently synced first."""
    conn = _db()
    rows = conn.execute(
        """
        SELECT DISTINCT w.user_id
        FROM workouts w
        LEFT JOIN sync_log s ON w.user_id = s.user_id
        ORDER BY s.last_synced DESC
        """
    ).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def count(user_id: str) -> int:
    conn = _db()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM workouts WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["n"]


def get_all(user_id: str) -> list[dict]:
    """Return all stored workouts for user, newest first."""
    conn = _db()
    rows = conn.execute(
        "SELECT data FROM workouts WHERE user_id = ? ORDER BY date DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [json.loads(r["data"]) for r in rows]


def get_newest_id(user_id: str) -> Optional[int]:
    """Return the ID of the newest stored workout, or None if the store is empty."""
    conn = _db()
    row = conn.execute(
        "SELECT id FROM workouts WHERE user_id = ? ORDER BY date DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return int(row["id"]) if row else None


def upsert(user_id: str, workouts: list[dict]):
    """Insert or replace a batch of normalized workout dicts."""
    if not workouts:
        return
    rows = [(user_id, w["id"], w["date"], json.dumps(w)) for w in workouts]
    conn = _db()
    conn.executemany(
        "INSERT OR REPLACE INTO workouts (user_id, id, date, data) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def last_synced(user_id: str) -> Optional[datetime]:
    conn = _db()
    row = conn.execute(
        "SELECT last_synced FROM sync_log WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return datetime.fromisoformat(row["last_synced"])


def set_synced(user_id: str):
    now = datetime.now(tz=timezone.utc).isoformat()
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO sync_log (user_id, last_synced) VALUES (?, ?)",
        (user_id, now),
    )
    conn.commit()
    conn.close()


def cache_get(key: str, ttl_seconds: int) -> Optional[dict]:
    """Return cached JSON data if present and within TTL, else None."""
    conn = _db()
    row = conn.execute(
        "SELECT data, fetched_at FROM cache WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age = (datetime.now(tz=timezone.utc) - fetched).total_seconds()
    if age > ttl_seconds:
        return None
    return json.loads(row["data"])


def cache_set(key: str, data: dict) -> None:
    """Store JSON-serializable data in the persistent cache with a UTC timestamp."""
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, data, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(data), datetime.now(tz=timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

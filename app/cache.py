"""Простой кеш и хранилище планов на SQLite. Без ORM — меньше движущихся частей."""
import json
import sqlite3
import threading
import time

from .config import DB_PATH

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires REAL)")
_conn.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, data TEXT, created REAL)")
_conn.commit()


def cache_get(key: str):
    with _lock:
        row = _conn.execute("SELECT value, expires FROM cache WHERE key = ?", (key,)).fetchone()
    if not row or row[1] < time.time():
        return None
    return json.loads(row[0])


def cache_set(key: str, value, ttl_seconds: int):
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time() + ttl_seconds),
        )
        _conn.commit()


def plan_save(plan_id: str, data: dict):
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO plans (id, data, created) VALUES (?, ?, ?)",
            (plan_id, json.dumps(data), time.time()),
        )
        _conn.commit()


def plan_get(plan_id: str):
    with _lock:
        row = _conn.execute("SELECT data FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return json.loads(row[0]) if row else None

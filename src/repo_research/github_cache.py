"""Persistent, process-safe cache for immutable GitHub Git objects.

Only the caller decides whether an object is immutable. Repository metadata and
branch names must never use an unlimited cache lifetime.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class GitHubCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), timeout=20, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=20000")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS github_cache "
            "(key TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL)"
        )
        self._db.commit()

    def get(self, key: str, ttl: float | None = None) -> Any | None:
        with self._lock:
            row = self._db.execute(
                "SELECT payload, created FROM github_cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            if ttl is not None and time.time() - row[1] > ttl:
                self.delete(key)
                return None
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                self.delete(key)
                return None

    def put(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._db.execute(
                "INSERT INTO github_cache(key, payload, created) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, created=excluded.created",
                (key, payload, time.time()),
            )
            self._db.commit()

    def delete(self, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM github_cache WHERE key = ?", (key,))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

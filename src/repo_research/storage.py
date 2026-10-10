"""Durable task metadata kept separate from LangGraph's execution checkpoints."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .models import Budget, ResearchRequest


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _task_id(value: str) -> str:
    """A task identifier is a canonical UUID, never a SQL/path expression."""
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise KeyError(value) from None
    if str(parsed) != value:
        raise KeyError(value)
    return value


class TaskStore:
    """One short-lived SQLite connection per operation is safe across workers.

    The server deliberately uses a single process. WAL and the busy timeout let
    HTTP reads proceed while workers persist progress. No live connection is
    passed from the web thread to a research worker.
    """

    MUTABLE_FIELDS = frozenset(
        {
            "status", "repository", "progress", "evidence", "usage", "report",
            "report_markdown", "error", "stop_reason", "pause_requested",
            "capabilities", "failed_node",
        }
    )

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "tasks.sqlite3"
        self.checkpoint_path = self.data_dir / "checkpoints.sqlite3"
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tasks ("
                "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS tasks_created_at ON tasks(created_at DESC)"
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _serialize(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    def create(
        self, request: ResearchRequest, budget: Budget, model_mode: str
    ) -> dict[str, Any]:
        if model_mode not in {"mock", "live"}:
            raise ValueError("Unsupported model mode.")
        now = _now()
        task = {
            "id": str(uuid4()), "status": "pending", "created_at": now, "updated_at": now,
            "request": request.model_dump(mode="json"), "budget": budget.model_dump(mode="json"),
            "model_mode": model_mode, "repository": None, "progress": [], "evidence": [],
            "usage": {}, "report": None, "report_markdown": "", "error": None,
            "stop_reason": "", "pause_requested": False, "capabilities": None,
            "failed_node": None,
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tasks(id, created_at, updated_at, status, data) VALUES (?, ?, ?, ?, ?)",
                (task["id"], now, now, task["status"], self._serialize(task)),
            )
        return task

    def get(self, task_id: str) -> dict[str, Any]:
        task_id = _task_id(task_id)
        with self._connect() as connection:
            row = connection.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return json.loads(row["data"])

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("History limit must be between 1 and 100.")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def patch(self, task_id: str, **fields: Any) -> dict[str, Any]:
        task_id = _task_id(task_id)
        if fields.keys() - self.MUTABLE_FIELDS:
            raise ValueError("Task identity, request and budget are immutable.")
        with self._connect() as connection:
            # Reserve the writer before reading, preventing two callbacks from
            # overwriting each other's fields with stale copies of the row.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = json.loads(row["data"])
            task.update(fields)
            task["updated_at"] = _now()
            connection.execute(
                "UPDATE tasks SET updated_at = ?, status = ?, data = ? WHERE id = ?",
                (task["updated_at"], task["status"], self._serialize(task), task_id),
            )
        return task

    def mark_interrupted(self) -> int:
        """Recover process-level interruption without resetting any spent budget."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, data FROM tasks WHERE status IN ('running', 'pending')"
            ).fetchall()
            now = _now()
            for row in rows:
                task = json.loads(row["data"])
                task.update(
                    status="interrupted", updated_at=now, pause_requested=False,
                    stop_reason="Server restarted. Resume manually from the saved checkpoint.",
                )
                connection.execute(
                    "UPDATE tasks SET updated_at = ?, status = ?, data = ? WHERE id = ?",
                    (now, "interrupted", self._serialize(task), row["id"]),
                )
        return len(rows)

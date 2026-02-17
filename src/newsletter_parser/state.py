"""SQLite-backed state management for tracking processed messages and run history."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class StateStore:
    """Persistent store for processed message IDs and run timestamps."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._init_schema()

    # ── Schema ───────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id   TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    ran_at              TEXT    NOT NULL,
                    messages_processed  INTEGER NOT NULL
                );
                """
            )

    # ── Message tracking ─────────────────────────────────────────────────

    def is_processed(self, message_id: str) -> bool:
        """Check if a message has already been processed."""
        row = self._conn.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def mark_processed(self, message_id: str) -> None:
        """Record a message as processed."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, processed_at) VALUES (?, ?)",
                (message_id, now),
            )

    def filter_unprocessed(self, message_ids: list[str]) -> list[str]:
        """Return only IDs that have not been processed yet."""
        return [mid for mid in message_ids if not self.is_processed(mid)]

    # ── Run history ──────────────────────────────────────────────────────

    def last_run_time(self) -> datetime | None:
        """Return the timestamp of the last successful run, or None if never run."""
        row = self._conn.execute(
            "SELECT ran_at FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row[0])

    def record_run(self, messages_processed: int) -> None:
        """Record a successful pipeline run."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (ran_at, messages_processed) VALUES (?, ?)",
                (now, messages_processed),
            )
        logger.info("Run recorded: %d messages processed", messages_processed)

    # ── Cleanup ──────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()

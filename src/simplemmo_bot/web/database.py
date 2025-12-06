"""SQLite database for bot statistics and settings."""

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

# Use environment variable or default to ./data/bot.db
_db_path = os.environ.get("BOT_DATABASE_PATH", "/app/data/bot.db")
DATABASE_PATH = Path(_db_path)


def get_db_path() -> Path:
    """Get database path, creating directory if needed."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DATABASE_PATH


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Get database connection as context manager."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database schema."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                status TEXT DEFAULT 'running',
                steps_taken INTEGER DEFAULT 0,
                npcs_fought INTEGER DEFAULT 0,
                npcs_won INTEGER DEFAULT 0,
                materials_gathered INTEGER DEFAULT 0,
                items_found INTEGER DEFAULT 0,
                gold_earned INTEGER DEFAULT 0,
                exp_earned INTEGER DEFAULT 0,
                quests_completed INTEGER DEFAULT 0,
                captchas_solved INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT,
                message TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_logs_session ON logs(session_id);
            CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC);
        """)
        conn.commit()


@dataclass
class SessionStats:
    """Current session statistics."""

    id: int
    started_at: datetime
    ended_at: datetime | None
    status: str
    steps_taken: int
    npcs_fought: int
    npcs_won: int
    materials_gathered: int
    items_found: int
    gold_earned: int
    exp_earned: int
    quests_completed: int
    captchas_solved: int
    errors: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SessionStats":
        """Create from database row."""
        return cls(
            id=row["id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            status=row["status"],
            steps_taken=row["steps_taken"],
            npcs_fought=row["npcs_fought"],
            npcs_won=row["npcs_won"],
            materials_gathered=row["materials_gathered"],
            items_found=row["items_found"],
            gold_earned=row["gold_earned"],
            exp_earned=row["exp_earned"],
            quests_completed=row["quests_completed"],
            captchas_solved=row["captchas_solved"],
            errors=row["errors"],
        )


def create_session() -> int:
    """Create new session and return its ID."""
    with get_connection() as conn:
        cursor = conn.execute("INSERT INTO sessions DEFAULT VALUES")
        conn.commit()
        return cursor.lastrowid


def update_session(
    session_id: int,
    steps_taken: int = 0,
    npcs_fought: int = 0,
    npcs_won: int = 0,
    materials_gathered: int = 0,
    items_found: int = 0,
    gold_earned: int = 0,
    exp_earned: int = 0,
    quests_completed: int = 0,
    captchas_solved: int = 0,
    errors: int = 0,
) -> None:
    """Update session statistics."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE sessions SET
                steps_taken = ?,
                npcs_fought = ?,
                npcs_won = ?,
                materials_gathered = ?,
                items_found = ?,
                gold_earned = ?,
                exp_earned = ?,
                quests_completed = ?,
                captchas_solved = ?,
                errors = ?
            WHERE id = ?
            """,
            (
                steps_taken,
                npcs_fought,
                npcs_won,
                materials_gathered,
                items_found,
                gold_earned,
                exp_earned,
                quests_completed,
                captchas_solved,
                errors,
                session_id,
            ),
        )
        conn.commit()


def end_session(session_id: int, status: str = "stopped") -> None:
    """Mark session as ended."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = CURRENT_TIMESTAMP, status = ? WHERE id = ?",
            (status, session_id),
        )
        conn.commit()


def get_current_session() -> SessionStats | None:
    """Get current running session."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE status = 'running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return SessionStats.from_row(row) if row else None


def get_total_stats() -> dict:
    """Get aggregated stats across all sessions."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total_sessions,
                COALESCE(SUM(steps_taken), 0) as total_steps,
                COALESCE(SUM(npcs_fought), 0) as total_npcs,
                COALESCE(SUM(npcs_won), 0) as total_npcs_won,
                COALESCE(SUM(materials_gathered), 0) as total_materials,
                COALESCE(SUM(items_found), 0) as total_items,
                COALESCE(SUM(gold_earned), 0) as total_gold,
                COALESCE(SUM(exp_earned), 0) as total_exp,
                COALESCE(SUM(quests_completed), 0) as total_quests,
                COALESCE(SUM(captchas_solved), 0) as total_captchas
            FROM sessions
            """
        ).fetchone()
        return dict(row)


def add_log(session_id: int | None, level: str, message: str) -> None:
    """Add log entry."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO logs (session_id, level, message) VALUES (?, ?, ?)",
            (session_id, level, message),
        )
        conn.commit()


def get_recent_logs(limit: int = 100) -> list[dict]:
    """Get recent log entries."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_setting(key: str, default: str = "") -> str:
    """Get setting value."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Set setting value."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
            """,
            (key, value, value),
        )
        conn.commit()

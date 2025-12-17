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
                account_id INTEGER,
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
                errors INTEGER DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
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

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                is_active INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_logs_session ON logs(session_id);
            CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC);
        """)
        conn.commit()

        # Migration: add level column if not exists
        try:
            conn.execute("ALTER TABLE accounts ADD COLUMN level INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: add auto_equip_best_items column if not exists
        try:
            conn.execute(
                "ALTER TABLE accounts ADD COLUMN auto_equip_best_items INTEGER DEFAULT 0"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: add account_id column to sessions if not exists
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN account_id INTEGER")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists


@dataclass
class SessionStats:
    """Current session statistics."""

    id: int
    account_id: int | None
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
        # Handle missing account_id for backwards compatibility
        account_id = None
        try:
            account_id = row["account_id"]
        except (IndexError, KeyError):
            pass

        return cls(
            id=row["id"],
            account_id=account_id,
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


def create_session(account_id: int | None = None) -> int:
    """Create new session and return its ID."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO sessions (account_id) VALUES (?)",
            (account_id,),
        )
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


# Account management
@dataclass
class Account:
    """Game account."""

    id: int
    name: str
    email: str
    password: str
    is_active: bool
    level: int
    created_at: str
    auto_equip_best_items: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Account":
        """Create from database row."""
        # Handle missing column for backwards compatibility
        auto_equip = False
        try:
            auto_equip = bool(row["auto_equip_best_items"])
        except (IndexError, KeyError):
            pass

        return cls(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            password=row["password"],
            is_active=bool(row["is_active"]),
            level=row["level"] or 0,
            created_at=row["created_at"],
            auto_equip_best_items=auto_equip,
        )


def get_accounts() -> list[Account]:
    """Get all accounts."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM accounts ORDER BY is_active DESC, name ASC"
        ).fetchall()
        return [Account.from_row(row) for row in rows]


def get_account(account_id: int) -> Account | None:
    """Get account by ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return Account.from_row(row) if row else None


def get_active_account() -> Account | None:
    """Get currently active account."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return Account.from_row(row) if row else None


def create_account(name: str, email: str, password: str) -> int:
    """Create new account and return its ID."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO accounts (name, email, password) VALUES (?, ?, ?)",
            (name, email, password),
        )
        conn.commit()
        return cursor.lastrowid


def update_account(
    account_id: int,
    name: str,
    email: str,
    password: str,
    auto_equip_best_items: bool = False,
) -> None:
    """Update account details."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE accounts
               SET name = ?, email = ?, password = ?, auto_equip_best_items = ?
               WHERE id = ?""",
            (name, email, password, int(auto_equip_best_items), account_id),
        )
        conn.commit()


def delete_account(account_id: int) -> None:
    """Delete account."""
    with get_connection() as conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()


def set_active_account(account_id: int) -> None:
    """Set account as active (deactivates all others)."""
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET is_active = 0")
        conn.execute("UPDATE accounts SET is_active = 1 WHERE id = ?", (account_id,))
        conn.commit()


def update_account_level(account_id: int, level: int) -> None:
    """Update account level."""
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET level = ? WHERE id = ?", (level, account_id))
        conn.commit()

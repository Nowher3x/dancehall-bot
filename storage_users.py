"""SQLite storage layer for Telegram bot users."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass


SECONDS_IN_DAY = 86_400
LIST_PAGE_SIZE = 20


@dataclass(slots=True)
class UserRecord:
    telegram_id: int
    username: str | None
    full_name: str | None
    expires_at: int | None
    is_banned: int
    note: str | None
    created_at: int
    updated_at: int


class UsersStorage:
    """CRUD and query helpers for the users table."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv("DB_PATH", "/app/data/dancehall.db")
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT NULL,
                full_name TEXT NULL,
                expires_at INTEGER NULL,
                is_banned INTEGER NOT NULL DEFAULT 0,
                note TEXT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_users_expires_at ON users(expires_at);
            CREATE INDEX IF NOT EXISTS idx_users_is_banned ON users(is_banned);
            """
        )
        self.conn.commit()

    def get_user(self, telegram_id: int):
        return self.conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    def upsert_user(
        self,
        telegram_id: int,
        username: str | None,
        full_name: str | None,
        expires_at: int | None,
        is_banned: int = 0,
        note: str | None = None,
    ) -> None:
        ts = int(time.time())
        self.conn.execute(
            """
            INSERT INTO users(telegram_id, username, full_name, expires_at, is_banned, note, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                expires_at = excluded.expires_at,
                is_banned = excluded.is_banned,
                note = COALESCE(excluded.note, users.note),
                updated_at = excluded.updated_at
            """,
            (telegram_id, username, full_name, expires_at, is_banned, note, ts, ts),
        )
        self.conn.commit()

    def update_expiration(self, telegram_id: int, expires_at: int | None) -> None:
        self.conn.execute(
            "UPDATE users SET expires_at = ?, updated_at = ? WHERE telegram_id = ?",
            (expires_at, int(time.time()), telegram_id),
        )
        self.conn.commit()

    def set_ban(self, telegram_id: int, is_banned: bool) -> None:
        self.conn.execute(
            "UPDATE users SET is_banned = ?, updated_at = ? WHERE telegram_id = ?",
            (1 if is_banned else 0, int(time.time()), telegram_id),
        )
        self.conn.commit()

    def delete_user(self, telegram_id: int) -> None:
        self.conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
        self.conn.commit()

    def list_active(self, now: int, offset: int):
        return self.conn.execute(
            """
            SELECT * FROM users
            WHERE is_banned = 0
              AND expires_at IS NOT NULL
              AND expires_at > ?
            ORDER BY expires_at ASC
            LIMIT ? OFFSET ?
            """,
            (now, LIST_PAGE_SIZE, offset),
        ).fetchall()

    def count_active(self, now: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM users
            WHERE is_banned = 0
              AND expires_at IS NOT NULL
              AND expires_at > ?
            """,
            (now,),
        ).fetchone()
        return int(row["cnt"])

    def list_expiring(self, now: int, until: int, offset: int):
        return self.conn.execute(
            """
            SELECT * FROM users
            WHERE is_banned = 0
              AND expires_at BETWEEN ? AND ?
            ORDER BY expires_at ASC
            LIMIT ? OFFSET ?
            """,
            (now, until, LIST_PAGE_SIZE, offset),
        ).fetchall()

    def count_expiring(self, now: int, until: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM users
            WHERE is_banned = 0
              AND expires_at BETWEEN ? AND ?
            """,
            (now, until),
        ).fetchone()
        return int(row["cnt"])

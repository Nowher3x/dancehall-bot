import re
import sqlite3
from math import ceil
from typing import Iterable

PAGE_SIZE = 10
CATEGORY_OPTIONS = [
    "Вайны",
    "Волны",
    "Тряски",
    "Передвижения",
    "Easy",
    "Hard",
    "Другое",
]


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    return u.rstrip("/")


class Storage:
    def __init__(self, path: str = "dancehall.db") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                file_id TEXT,
                file_unique_id TEXT UNIQUE,
                source_url TEXT,
                source_url_normalized TEXT UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS video_categories (
                video_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                PRIMARY KEY(video_id, category_id),
                FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                video_id INTEGER NOT NULL,
                PRIMARY KEY(user_id, video_id),
                FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
            DROP TABLE IF EXISTS video_tags;
            DROP TABLE IF EXISTS tags;
            """
        )
        self.conn.commit()

    def ensure_taxonomy(self) -> None:
        for name in CATEGORY_OPTIONS:
            self.conn.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (name,))
        self.conn.commit()

    def find_video_by_file_uid(self, uid: str):
        return self.conn.execute("SELECT * FROM videos WHERE file_unique_id = ?", (uid,)).fetchone()

    def find_video_by_url(self, normalized_url: str):
        return self.conn.execute(
            "SELECT * FROM videos WHERE source_url_normalized = ?", (normalized_url,)
        ).fetchone()

    def find_video_by_title(self, title: str):
        return self.conn.execute("SELECT * FROM videos WHERE lower(title) = lower(?)", (title.strip(),)).fetchone()

    def create_video(self, title, file_id, file_unique_id, source_url, categories):
        normalized_url = normalize_url(source_url) if source_url else None
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO videos(title, file_id, file_unique_id, source_url, source_url_normalized)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title.strip(), file_id, file_unique_id, source_url, normalized_url),
        )
        vid = cur.lastrowid
        self._set_categories(vid, categories)
        self.conn.commit()
        return vid

    def replace_video(self, video_id, title, file_id, file_unique_id, source_url, categories):
        normalized_url = normalize_url(source_url) if source_url else None
        self.conn.execute(
            """
            UPDATE videos
               SET title = ?, file_id = ?, file_unique_id = ?, source_url = ?, source_url_normalized = ?
             WHERE id = ?
            """,
            (title.strip(), file_id, file_unique_id, source_url, normalized_url, video_id),
        )
        self._set_categories(video_id, categories)
        self.conn.commit()

    def _set_categories(self, video_id: int, categories: Iterable[str]) -> None:
        self.conn.execute("DELETE FROM video_categories WHERE video_id = ?", (video_id,))
        for c in categories:
            cid = self._ensure_entity("categories", c)
            self.conn.execute(
                "INSERT OR IGNORE INTO video_categories(video_id, category_id) VALUES(?, ?)",
                (video_id, cid),
            )

    def _ensure_entity(self, table: str, name: str) -> int:
        self.conn.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES(?)", (name.strip(),))
        row = self.conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name.strip(),)).fetchone()
        return int(row["id"])

    def get_video(self, video_id: int):
        return self.conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()

    def video_categories(self, video_id: int) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT c.name FROM categories c
            JOIN video_categories vc ON vc.category_id = c.id
            WHERE vc.video_id = ?
            ORDER BY c.name
            """,
            (video_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def search(self, filter_type: str, query: str, page: int):
        offset = page * PAGE_SIZE
        q = query.strip()
        if filter_type == "title":
            base = "SELECT * FROM videos WHERE lower(title) LIKE ? ORDER BY id DESC"
            arg = (f"%{q.lower()}%",)
            total = self.conn.execute(f"SELECT COUNT(*) AS cnt FROM ({base})", arg).fetchone()["cnt"]
            rows = self.conn.execute(f"{base} LIMIT ? OFFSET ?", (*arg, PAGE_SIZE, offset)).fetchall()
            pages = ceil(total / PAGE_SIZE) if total else 0
            return rows, pages

        all_rows = self.conn.execute(
            """
            SELECT DISTINCT v.* FROM videos v
            JOIN video_categories vc ON vc.video_id = v.id
            JOIN categories c ON c.id = vc.category_id
            ORDER BY v.id DESC
            """
        ).fetchall()
        filtered = [r for r in all_rows if q.casefold() in " ".join(self.video_categories(r["id"])).casefold()]
        total = len(filtered)
        rows = filtered[offset : offset + PAGE_SIZE]
        pages = ceil(total / PAGE_SIZE) if total else 0
        return rows, pages

    def toggle_favorite(self, user_id: int, video_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND video_id = ?", (user_id, video_id)
        ).fetchone()
        if row:
            self.conn.execute("DELETE FROM favorites WHERE user_id = ? AND video_id = ?", (user_id, video_id))
            self.conn.commit()
            return False
        self.conn.execute("INSERT INTO favorites(user_id, video_id) VALUES(?, ?)", (user_id, video_id))
        self.conn.commit()
        return True

    def is_favorite(self, user_id: int, video_id: int) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM favorites WHERE user_id = ? AND video_id = ?", (user_id, video_id)
            ).fetchone()
            is not None
        )

    def favorites(self, user_id: int, page: int):
        offset = page * PAGE_SIZE
        total = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM favorites WHERE user_id = ?", (user_id,)
        ).fetchone()["cnt"]
        rows = self.conn.execute(
            """
            SELECT v.* FROM videos v
            JOIN favorites f ON f.video_id = v.id
            WHERE f.user_id = ?
            ORDER BY v.id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, PAGE_SIZE, offset),
        ).fetchall()
        return rows, ceil(total / PAGE_SIZE) if total else 0

    def list_all_videos(self, page: int):
        offset = page * PAGE_SIZE
        total = self.conn.execute("SELECT COUNT(*) AS cnt FROM videos").fetchone()["cnt"]
        rows = self.conn.execute(
            "SELECT * FROM videos ORDER BY id DESC LIMIT ? OFFSET ?", (PAGE_SIZE, offset)
        ).fetchall()
        return rows, ceil(total / PAGE_SIZE) if total else 0

    def update_title(self, video_id: int, title: str) -> None:
        self.conn.execute("UPDATE videos SET title = ? WHERE id = ?", (title.strip(), video_id))
        self.conn.commit()

    def delete_video(self, video_id: int) -> None:
        self.conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        self.conn.commit()

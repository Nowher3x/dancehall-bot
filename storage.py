import re
import sqlite3
from math import ceil
from typing import Iterable, Optional
import os

PAGE_SIZE = 10
TITLE_LIST_PAGE_SIZE = 15
CATEGORY_OPTIONS = [
    "Вайны",
    "Волны",
    "Степовые треки",
    "Тряски",
    "Передвижения",
    "Прыжки",
    "Easy",
    "Hard",
]


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    return u.rstrip("/")

class Storage:
    def __init__(self, path: Optional[str] = None) -> None:
        if path is None:
            path = os.getenv("DB_PATH", "/app/data/dancehall.db")
            print(f"[DB] Using DB_PATH={path}")
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON;")
        self.ensure_videos_schema()
        cur.executescript(
            """
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
        self._repair_relations_after_video_rebuild()
        self.conn.commit()


    def _repair_relations_after_video_rebuild(self) -> None:
        video_categories_sql = self._table_sql("video_categories")
        favorites_sql = self._table_sql("favorites")
        needs_video_categories_fix = video_categories_sql and "videos_old" in video_categories_sql.lower()
        needs_favorites_fix = favorites_sql and "videos_old" in favorites_sql.lower()
        if not needs_video_categories_fix and not needs_favorites_fix:
            return

        print(
            "[DB] repairing relation tables after videos rebuild: "
            f"video_categories={needs_video_categories_fix}, favorites={needs_favorites_fix}"
        )

        if needs_video_categories_fix:
            self.conn.executescript(
                """
                CREATE TABLE video_categories_new (
                    video_id INTEGER NOT NULL,
                    category_id INTEGER NOT NULL,
                    PRIMARY KEY(video_id, category_id),
                    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                    FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
                );
                INSERT OR IGNORE INTO video_categories_new(video_id, category_id)
                SELECT vc.video_id, vc.category_id
                  FROM video_categories vc
                  JOIN videos v ON v.id = vc.video_id
                  JOIN categories c ON c.id = vc.category_id;
                DROP TABLE video_categories;
                ALTER TABLE video_categories_new RENAME TO video_categories;
                """
            )

        if needs_favorites_fix:
            self.conn.executescript(
                """
                CREATE TABLE favorites_new (
                    user_id INTEGER NOT NULL,
                    video_id INTEGER NOT NULL,
                    PRIMARY KEY(user_id, video_id),
                    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
                );
                INSERT OR IGNORE INTO favorites_new(user_id, video_id)
                SELECT f.user_id, f.video_id
                  FROM favorites f
                  JOIN videos v ON v.id = f.video_id;
                DROP TABLE favorites;
                ALTER TABLE favorites_new RENAME TO favorites;
                """
            )

    def _table_sql(self, table_name: str) -> str | None:
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row["sql"] if row else None

    def ensure_videos_schema(self) -> None:
        self._ensure_db_health()
        if not self._videos_table_exists():
            print("[DB] videos table not found, creating fresh schema")
            self._create_videos_table()
            self.conn.commit()
            return

        indexes = self._get_video_indexes()
        columns = self._get_video_columns()
        has_vault_cols = {"vault_chat_id", "vault_message_id"}.issubset(columns)
        has_unique_file_unique_id = any(
            idx["unique"] and "file_unique_id" in idx["columns"] for idx in indexes
        )
        has_unique_vault_pair = any(
            idx["unique"]
            and "vault_chat_id" in idx["columns"]
            and "vault_message_id" in idx["columns"]
            for idx in indexes
        )

        print(
            f"[DB] videos schema check: columns={sorted(columns)} indexes={indexes}"
        )
        needs_migration = not (has_unique_vault_pair and not has_unique_file_unique_id and has_vault_cols)
        print(
            "[DB] videos migration required="
            f"{needs_migration} (has_vault_cols={has_vault_cols}, "
            f"has_unique_file_unique_id={has_unique_file_unique_id}, "
            f"has_unique_vault_pair={has_unique_vault_pair})"
        )
        if not needs_migration:
            return

        self._rebuild_videos_table()

    def _ensure_db_health(self) -> None:
        self.conn.execute("SELECT 1").fetchone()
        integrity = self.conn.execute("PRAGMA integrity_check").fetchone()
        result = (integrity[0] if integrity else "").lower()
        if result != "ok":
            print(f"[DB] integrity_check failed: {result}")
            raise RuntimeError("SQLite integrity check failed; aborting schema migration")

    def _videos_table_exists(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'videos'"
        ).fetchone()
        return row is not None

    def _get_video_columns(self) -> set[str]:
        return {row["name"] for row in self.conn.execute("PRAGMA table_info(videos)").fetchall()}

    def _get_video_indexes(self) -> list[dict]:
        data = []
        for idx in self.conn.execute("PRAGMA index_list(videos)").fetchall():
            cols = [
                r["name"]
                for r in self.conn.execute(f"PRAGMA index_info('{idx['name']}')").fetchall()
            ]
            data.append({"name": idx["name"], "unique": bool(idx["unique"]), "columns": cols})
        return data

    def _create_videos_table(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                file_id TEXT,
                file_unique_id TEXT,
                source_url TEXT,
                source_url_normalized TEXT UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                storage_chat_id INTEGER,
                storage_message_id INTEGER,
                needs_refresh INTEGER NOT NULL DEFAULT 0,
                vault_chat_id INTEGER,
                vault_message_id INTEGER
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_videos_vault_pair
              ON videos(vault_chat_id, vault_message_id);
            CREATE INDEX IF NOT EXISTS ix_videos_created_at ON videos(created_at);
            CREATE INDEX IF NOT EXISTS ix_videos_vault_chat_id ON videos(vault_chat_id);
            """
        )

    def _rebuild_videos_table(self) -> None:
        rows_before = self.conn.execute("SELECT COUNT(*) AS cnt FROM videos").fetchone()["cnt"]
        print(f"[DB] rebuilding videos table, rows_before={rows_before}")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("ALTER TABLE videos RENAME TO videos_old")
            self._create_videos_table()
            self.conn.execute(
                """
                INSERT OR IGNORE INTO videos(
                    id, title, file_id, file_unique_id, source_url, source_url_normalized,
                    created_at, storage_chat_id, storage_message_id, needs_refresh,
                    vault_chat_id, vault_message_id
                )
                SELECT
                    id, title, file_id, file_unique_id, source_url, source_url_normalized,
                    created_at, storage_chat_id, storage_message_id,
                    COALESCE(needs_refresh, 0),
                    COALESCE(vault_chat_id, storage_chat_id),
                    COALESCE(vault_message_id, storage_message_id)
                FROM videos_old
                """
            )
            inserted_rows = self.conn.execute("SELECT COUNT(*) AS cnt FROM videos").fetchone()["cnt"]
            skipped_rows = rows_before - inserted_rows
            self.conn.execute("DROP TABLE videos_old")
            self.conn.execute("COMMIT")
            print(
                "[DB] videos rebuild complete: "
                f"inserted_rows={inserted_rows}, skipped_rows={skipped_rows}"
            )
        except Exception as exc:
            self.conn.execute("ROLLBACK")
            print(f"[DB] videos rebuild failed, rolled back: {exc}")
            raise

        final_indexes = self._get_video_indexes()
        has_unique_file_unique_id = any(
            idx["unique"] and "file_unique_id" in idx["columns"] for idx in final_indexes
        )
        has_unique_vault_pair = any(
            idx["unique"]
            and "vault_chat_id" in idx["columns"]
            and "vault_message_id" in idx["columns"]
            for idx in final_indexes
        )
        rows_after = self.conn.execute("SELECT COUNT(*) AS cnt FROM videos").fetchone()["cnt"]
        print(
            f"[DB] videos post-check: rows_after={rows_after}, indexes={final_indexes}, "
            f"has_unique_vault_pair={has_unique_vault_pair}, "
            f"has_unique_file_unique_id={has_unique_file_unique_id}"
        )
        if not has_unique_vault_pair or has_unique_file_unique_id:
            raise RuntimeError("videos schema post-check failed")

    def ensure_taxonomy(self) -> None:
        for name in CATEGORY_OPTIONS:
            self.conn.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (name,))
        self.conn.execute("DELETE FROM categories WHERE name = ?", ("Другое",))
        self.conn.commit()

    def find_video_by_file_uid(self, uid: str):
        return self.conn.execute(
            "SELECT * FROM videos WHERE file_unique_id = ? ORDER BY id DESC LIMIT 1",
            (uid,),
        ).fetchone()

    def find_video_by_storage_message(self, storage_message_id: int):
        return self.conn.execute(
            "SELECT * FROM videos WHERE storage_message_id = ?", (storage_message_id,)
        ).fetchone()


    def find_video_by_vault_message(self, vault_message_id: int):
        return self.conn.execute(
            "SELECT * FROM videos WHERE vault_message_id = ?", (vault_message_id,)
        ).fetchone()

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

    def upsert_video_file(self, title, file_id, file_unique_id, source_url):
        normalized_url = normalize_url(source_url) if source_url else None
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO videos(title, file_id, file_unique_id, source_url, source_url_normalized, needs_refresh)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (title.strip(), file_id, file_unique_id, source_url, normalized_url),
        )
        row_id = cur.lastrowid
        self.conn.commit()
        return row_id

    def upsert_video_by_vault(self, title, file_id, file_unique_id, source_url, vault_chat_id, vault_message_id):
        normalized_url = normalize_url(source_url) if source_url else None
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO videos(
                title, file_id, file_unique_id, source_url, source_url_normalized,
                needs_refresh, vault_chat_id, vault_message_id, storage_chat_id, storage_message_id
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(vault_chat_id, vault_message_id) DO UPDATE SET
                title = excluded.title,
                file_id = excluded.file_id,
                file_unique_id = excluded.file_unique_id,
                source_url = excluded.source_url,
                source_url_normalized = excluded.source_url_normalized,
                needs_refresh = 0,
                storage_chat_id = excluded.storage_chat_id,
                storage_message_id = excluded.storage_message_id
            """,
            (
                title.strip(),
                file_id,
                file_unique_id,
                source_url,
                normalized_url,
                vault_chat_id,
                vault_message_id,
                vault_chat_id,
                vault_message_id,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM videos WHERE vault_chat_id = ? AND vault_message_id = ?",
            (vault_chat_id, vault_message_id),
        ).fetchone()
        self.conn.commit()
        return int(row["id"])

    def save_storage_message(self, video_id: int, storage_chat_id: int, storage_message_id: int) -> None:
        self.conn.execute(
            """
            UPDATE videos
               SET storage_chat_id = ?, storage_message_id = ?
             WHERE id = ?
            """,
            (storage_chat_id, storage_message_id, video_id),
        )
        self.conn.commit()

    def save_vault_message(self, video_id: int, vault_chat_id: int, vault_message_id: int) -> None:
        self.conn.execute(
            """
            UPDATE videos
               SET vault_chat_id = ?, vault_message_id = ?, storage_chat_id = ?, storage_message_id = ?
             WHERE id = ?
            """,
            (vault_chat_id, vault_message_id, vault_chat_id, vault_message_id, video_id),
        )
        self.conn.commit()

    def refresh_file_id_from_storage(self, storage_message_id: int, file_id: str, file_unique_id: str) -> int | None:
        updated = self.conn.execute(
            """
            UPDATE videos
               SET file_id = ?, file_unique_id = ?, needs_refresh = 0
             WHERE storage_message_id = ?
            """,
            (file_id, file_unique_id, storage_message_id),
        )
        if updated.rowcount:
            self.conn.commit()
            return updated.rowcount

        fallback = self.conn.execute(
            """
            UPDATE videos
               SET file_id = ?, needs_refresh = 0
             WHERE file_unique_id = ?
            """,
            (file_id, file_unique_id),
        )
        self.conn.commit()
        return fallback.rowcount if fallback.rowcount else None

    def mark_needs_refresh(self, video_id: int) -> None:
        self.conn.execute("UPDATE videos SET needs_refresh = 1 WHERE id = ?", (video_id,))
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

    def list_titles(self, page: int):
        offset = page * TITLE_LIST_PAGE_SIZE
        total = self.conn.execute("SELECT COUNT(*) AS cnt FROM videos").fetchone()["cnt"]
        rows = self.conn.execute(
            "SELECT id, title FROM videos ORDER BY id DESC LIMIT ? OFFSET ?",
            (TITLE_LIST_PAGE_SIZE, offset),
        ).fetchall()
        return rows, ceil(total / TITLE_LIST_PAGE_SIZE) if total else 0

    def update_title(self, video_id: int, title: str) -> None:
        self.conn.execute("UPDATE videos SET title = ? WHERE id = ?", (title.strip(), video_id))
        self.conn.commit()

    def delete_video(self, video_id: int) -> None:
        self.conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        self.conn.commit()

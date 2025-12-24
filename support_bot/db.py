from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

import aiosqlite


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass(frozen=True)
class Conversation:
    user_id: int
    topic_id: int
    active: bool


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def init(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              user_id      INTEGER PRIMARY KEY,
              username     TEXT,
              first_name   TEXT,
              last_name    TEXT,
              created_at   TEXT NOT NULL,
              updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
              user_id      INTEGER PRIMARY KEY,
              topic_id     INTEGER NOT NULL,
              active       INTEGER NOT NULL DEFAULT 1,
              created_at   TEXT NOT NULL,
              updated_at   TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_topic_id
              ON conversations(topic_id);

            CREATE TABLE IF NOT EXISTS messages (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id      INTEGER NOT NULL,
              direction    TEXT NOT NULL,   -- 'user' or 'operator'
              chat_id      INTEGER NOT NULL,
              message_id   INTEGER NOT NULL,
              content_type TEXT NOT NULL,
              text         TEXT,
              caption      TEXT,
              file_id      TEXT,
              payload_json TEXT,
              created_at   TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user_id_created_at
              ON messages(user_id, created_at);
            """
        )
        await self.conn.commit()

    async def upsert_user(
        self,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
    ) -> None:
        now = _now_iso()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,
              first_name=excluded.first_name,
              last_name=excluded.last_name,
              updated_at=excluded.updated_at
            """,
            (user_id, username, first_name, last_name, now, now),
        )
        await self.conn.commit()

    async def get_active_conversation(self, user_id: int) -> Conversation | None:
        cur = await self.conn.execute(
            "SELECT user_id, topic_id, active FROM conversations WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        conversation = Conversation(user_id=int(row[0]), topic_id=int(row[1]), active=bool(row[2]))
        if not conversation.active:
            return None
        return conversation

    async def set_conversation(self, user_id: int, topic_id: int, active: bool = True) -> None:
        now = _now_iso()
        await self.conn.execute(
            """
            INSERT INTO conversations (user_id, topic_id, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              topic_id=excluded.topic_id,
              active=excluded.active,
              updated_at=excluded.updated_at
            """,
            (user_id, topic_id, 1 if active else 0, now, now),
        )
        await self.conn.commit()

    async def deactivate_conversation(self, user_id: int) -> None:
        now = _now_iso()
        await self.conn.execute(
            "UPDATE conversations SET active=0, updated_at=? WHERE user_id=?",
            (now, user_id),
        )
        await self.conn.commit()

    async def find_user_id_by_topic(self, topic_id: int) -> int | None:
        cur = await self.conn.execute(
            "SELECT user_id FROM conversations WHERE topic_id = ? AND active = 1",
            (topic_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else None

    async def log_message(
        self,
        *,
        user_id: int,
        direction: str,
        chat_id: int,
        message_id: int,
        content_type: str,
        text: str | None,
        caption: str | None,
        file_id: str | None,
        payload_json: str | None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO messages (
              user_id, direction, chat_id, message_id, content_type,
              text, caption, file_id, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                direction,
                chat_id,
                message_id,
                content_type,
                text,
                caption,
                file_id,
                payload_json,
                _now_iso(),
            ),
        )
        await self.conn.commit()

    async def healthcheck(self) -> dict[str, Any]:
        cur = await self.conn.execute("SELECT 1;")
        row = await cur.fetchone()
        await cur.close()
        return {"ok": row == (1,)}


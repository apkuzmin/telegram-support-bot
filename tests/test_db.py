import asyncio
import sqlite3
import tempfile
from unittest import IsolatedAsyncioTestCase

from support_bot.db import Database


class DatabaseConcurrencyTests(IsolatedAsyncioTestCase):
    async def test_unrelated_write_cannot_commit_transaction_before_rollback(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            db = Database(db_file.name)
            await db.connect()
            try:
                await db.init()
                await db.upsert_user(1, "one", "One", None)

                link_inserted = asyncio.Event()
                release_transaction = asyncio.Event()
                unrelated_write_done = asyncio.Event()

                async def failing_transaction() -> None:
                    try:
                        async with db.transaction():
                            await db.log_message_link(
                                user_id=1,
                                source_chat_id=1,
                                source_message_id=1,
                                target_chat_id=2,
                                target_message_id=2,
                                commit=False,
                            )
                            link_inserted.set()
                            await release_transaction.wait()
                            raise RuntimeError("rollback")
                    except RuntimeError:
                        pass

                async def unrelated_write() -> None:
                    await link_inserted.wait()
                    await db.upsert_user(2, "two", "Two", None)
                    unrelated_write_done.set()

                transaction_task = asyncio.create_task(failing_transaction())
                write_task = asyncio.create_task(unrelated_write())

                await link_inserted.wait()
                await asyncio.sleep(0.01)
                self.assertFalse(unrelated_write_done.is_set())

                release_transaction.set()
                await asyncio.gather(transaction_task, write_task)

                cursor = await db.conn.execute("SELECT COUNT(*) FROM message_links")
                links_count = (await cursor.fetchone())[0]
                await cursor.close()
                self.assertEqual(links_count, 0)

                cursor = await db.conn.execute(
                    "SELECT COUNT(*) FROM users WHERE user_id = 2"
                )
                user_count = (await cursor.fetchone())[0]
                await cursor.close()
                self.assertEqual(user_count, 1)
            finally:
                await db.close()

    async def test_commit_false_requires_explicit_transaction(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            db = Database(db_file.name)
            await db.connect()
            try:
                await db.init()
                with self.assertRaisesRegex(
                    RuntimeError,
                    "commit=False requires Database.transaction",
                ):
                    await db.upsert_user(
                        1,
                        "one",
                        "One",
                        None,
                        commit=False,
                    )
            finally:
                await db.close()

    async def test_implicit_commit_is_rejected_inside_transaction(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            db = Database(db_file.name)
            await db.connect()
            try:
                await db.init()
                async with db.transaction():
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "Use commit=False inside Database.transaction",
                    ):
                        await db.upsert_user(1, "one", "One", None)
            finally:
                await db.close()

    async def test_logged_message_is_updated_after_edit(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            db = Database(db_file.name)
            await db.connect()
            try:
                await db.init()
                await db.log_user_message(
                    user_id=1,
                    username="one",
                    first_name="One",
                    last_name=None,
                    direction="user",
                    chat_id=1,
                    message_id=10,
                    content_type="text",
                    text="До изменения",
                    caption=None,
                    file_id=None,
                    payload_json='{"text":"До изменения"}',
                )

                await db.update_logged_message(
                    chat_id=1,
                    message_id=10,
                    content_type="text",
                    text="После изменения",
                    caption=None,
                    file_id=None,
                    payload_json='{"text":"После изменения"}',
                )

                cursor = await db.conn.execute(
                    """
                    SELECT text, payload_json, edited_at
                      FROM messages
                     WHERE chat_id = 1 AND message_id = 10
                    """
                )
                row = await cursor.fetchone()
                await cursor.close()
                self.assertEqual(row[0], "После изменения")
                self.assertEqual(row[1], '{"text":"После изменения"}')
                self.assertIsNotNone(row[2])
            finally:
                await db.close()

    async def test_existing_messages_table_gets_edited_at_column(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            legacy = sqlite3.connect(db_file.name)
            legacy.execute(
                """
                CREATE TABLE messages (
                  id           INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id      INTEGER NOT NULL,
                  direction    TEXT NOT NULL,
                  chat_id      INTEGER NOT NULL,
                  message_id   INTEGER NOT NULL,
                  content_type TEXT NOT NULL,
                  text         TEXT,
                  caption      TEXT,
                  file_id      TEXT,
                  payload_json TEXT,
                  created_at   TEXT NOT NULL
                )
                """
            )
            legacy.commit()
            legacy.close()

            db = Database(db_file.name)
            await db.connect()
            try:
                await db.init()
                cursor = await db.conn.execute("PRAGMA table_info(messages)")
                columns = {row[1] for row in await cursor.fetchall()}
                await cursor.close()
                self.assertIn("edited_at", columns)
            finally:
                await db.close()

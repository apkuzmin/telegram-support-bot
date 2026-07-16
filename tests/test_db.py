import asyncio
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

import tempfile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from support_bot.db import Database
from support_bot.handlers.user import DELIVERY_ERROR_TEXT, start
from support_bot.topic_manager import MessageDeliveryError, TopicManager


class StartHandlerTests(IsolatedAsyncioTestCase):
    async def test_start_uses_configured_message_and_keeps_routing_without_history(self) -> None:
        user = SimpleNamespace(
            id=42,
            username="ivan",
            first_name="Иван",
            last_name="Иванов",
        )
        message = SimpleNamespace(
            from_user=user,
            answer=AsyncMock(),
        )
        db = SimpleNamespace(
            upsert_user=AsyncMock(),
            log_user_message=AsyncMock(),
        )
        topics = SimpleNamespace(copy_user_message_to_topic=AsyncMock())
        bot = object()

        await start(
            message=message,
            bot=bot,
            db=db,
            topics=topics,
            log_messages=False,
            start_message="Configured welcome",
        )

        message.answer.assert_awaited_once_with("Configured welcome")
        db.upsert_user.assert_awaited_once_with(
            user_id=42,
            username="ivan",
            first_name="Иван",
            last_name="Иванов",
        )
        db.log_user_message.assert_not_awaited()
        topics.copy_user_message_to_topic.assert_awaited_once_with(bot, message)

    async def test_start_reports_delivery_failure_without_success_greeting(self) -> None:
        user = SimpleNamespace(
            id=42,
            username="ivan",
            first_name="Иван",
            last_name="Иванов",
        )
        message = SimpleNamespace(
            from_user=user,
            answer=AsyncMock(),
        )
        db = SimpleNamespace(
            upsert_user=AsyncMock(),
            log_user_message=AsyncMock(),
        )
        topics = SimpleNamespace(
            copy_user_message_to_topic=AsyncMock(
                side_effect=MessageDeliveryError("delivery failed")
            )
        )

        await start(
            message=message,
            bot=object(),
            db=db,
            topics=topics,
            log_messages=False,
        )

        message.answer.assert_awaited_once_with(DELIVERY_ERROR_TEXT)

    async def test_disabled_history_still_allows_conversation_routing(self) -> None:
        user = SimpleNamespace(
            id=42,
            username="ivan",
            first_name="Иван",
            last_name="Иванов",
            full_name="Иван Иванов",
        )
        message = SimpleNamespace(
            from_user=user,
            chat=SimpleNamespace(id=42),
            message_id=10,
            reply_to_message=None,
            content_type="text",
            text="/start",
            entities=(),
            answer=AsyncMock(),
        )
        bot = SimpleNamespace(
            create_forum_topic=AsyncMock(
                return_value=SimpleNamespace(message_thread_id=77)
            ),
            send_message=AsyncMock(),
            copy_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        )

        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            db = Database(db_file.name)
            await db.connect()
            try:
                await db.init()
                topics = TopicManager(db=db, operator_group_id=-1001)

                await start(
                    message=message,
                    bot=bot,
                    db=db,
                    topics=topics,
                    log_messages=False,
                )

                conversation = await db.get_active_conversation(user.id)
                self.assertIsNotNone(conversation)
                self.assertEqual(conversation.topic_id, 77)
            finally:
                await db.close()

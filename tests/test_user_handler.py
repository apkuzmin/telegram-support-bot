import tempfile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from support_bot.db import Database
from support_bot.handlers.user import (
    DELIVERY_ERROR_TEXT,
    EDIT_ERROR_TEXT,
    edited_private_message,
    start,
)
from support_bot.message_editor import EditSyncStatus, MessageEditError
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

    async def test_edited_user_message_updates_history_and_operator_copy(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42),
            message_id=10,
            content_type="text",
            text="Исправленный текст",
            caption=None,
            photo=None,
            document=None,
            video=None,
            audio=None,
            voice=None,
            sticker=None,
            animation=None,
            video_note=None,
        )
        db = SimpleNamespace(update_logged_message=AsyncMock())
        topics = SimpleNamespace(operator_group_id=-1001)

        with patch(
            "support_bot.handlers.user.sync_edited_message",
            new=AsyncMock(return_value=EditSyncStatus.SYNCED),
        ) as sync:
            await edited_private_message(
                message=message,
                bot=object(),
                db=db,
                topics=topics,
                log_messages=True,
            )

        db.update_logged_message.assert_awaited_once_with(
            chat_id=42,
            message_id=10,
            content_type="text",
            text="Исправленный текст",
            caption=None,
            file_id=None,
            payload_json=None,
        )
        sync.assert_awaited_once()
        self.assertEqual(sync.await_args.kwargs["target_chat_id"], -1001)

    async def test_edited_user_message_reports_sync_failure(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42),
            message_id=10,
            answer=AsyncMock(),
        )
        db = SimpleNamespace()
        topics = SimpleNamespace(operator_group_id=-1001)

        with patch(
            "support_bot.handlers.user.sync_edited_message",
            new=AsyncMock(side_effect=MessageEditError("failed")),
        ):
            await edited_private_message(
                message=message,
                bot=object(),
                db=db,
                topics=topics,
                log_messages=False,
            )

        message.answer.assert_awaited_once_with(EDIT_ERROR_TEXT)

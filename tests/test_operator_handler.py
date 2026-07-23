from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from aiogram.types import MessageEntity

from support_bot.handlers.operator import (
    edited_topic_message_to_user,
    topic_message_to_user,
)
from support_bot.message_editor import EditSyncStatus, MessageEditError


class OperatorHandlerTests(IsolatedAsyncioTestCase):
    async def test_operator_message_is_copied_without_rebuilding_text(self) -> None:
        @asynccontextmanager
        async def transaction():
            yield

        message = SimpleNamespace(
            from_user=SimpleNamespace(is_bot=False),
            message_thread_id=77,
            reply_to_message=None,
            chat=SimpleNamespace(id=-1001),
            message_id=10,
            content_type="text",
            text="Жирный текст",
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
        bot = SimpleNamespace(
            copy_message=AsyncMock(return_value=SimpleNamespace(message_id=99))
        )
        db = SimpleNamespace(
            find_user_id_by_topic=AsyncMock(return_value=42),
            log_message_link=AsyncMock(),
            transaction=transaction,
        )
        admin_bridge = SimpleNamespace(publish_operator_message=AsyncMock())

        await topic_message_to_user(
            message=message,
            bot=bot,
            db=db,
            log_messages=False,
            admin_bridge=admin_bridge,
        )

        bot.copy_message.assert_awaited_once_with(
            chat_id=42,
            from_chat_id=-1001,
            message_id=10,
            reply_parameters=None,
        )
        admin_bridge.publish_operator_message.assert_awaited_once_with(
            message, 42, db
        )

    async def test_operator_reply_passes_selected_quote_to_user(self) -> None:
        @asynccontextmanager
        async def transaction():
            yield

        quote_entities = [MessageEntity(type="italic", offset=0, length=8)]
        message = SimpleNamespace(
            from_user=SimpleNamespace(is_bot=False),
            message_thread_id=77,
            reply_to_message=SimpleNamespace(message_id=9),
            quote=SimpleNamespace(
                text="Выделено",
                entities=quote_entities,
                position=3,
            ),
            chat=SimpleNamespace(id=-1001),
            message_id=10,
            content_type="text",
        )
        bot = SimpleNamespace(
            copy_message=AsyncMock(return_value=SimpleNamespace(message_id=99))
        )
        db = SimpleNamespace(
            find_user_id_by_topic=AsyncMock(return_value=42),
            find_linked_message_id=AsyncMock(return_value=8),
            log_message_link=AsyncMock(),
            transaction=transaction,
        )

        await topic_message_to_user(
            message=message,
            bot=bot,
            db=db,
            log_messages=False,
        )

        reply = bot.copy_message.await_args.kwargs["reply_parameters"]
        self.assertEqual(reply.message_id, 8)
        self.assertEqual(reply.quote, "Выделено")
        self.assertEqual(reply.quote_entities, quote_entities)
        self.assertEqual(reply.quote_position, 3)

    async def test_operator_copy_path_supports_common_media_types(self) -> None:
        content_types = (
            "photo",
            "video",
            "sticker",
            "animation",
            "audio",
            "document",
            "voice",
            "video_note",
            "contact",
            "location",
            "venue",
            "poll",
            "dice",
            "game",
        )

        for content_type in content_types:
            with self.subTest(content_type=content_type):
                @asynccontextmanager
                async def transaction():
                    yield

                message = SimpleNamespace(
                    from_user=SimpleNamespace(is_bot=False),
                    message_thread_id=77,
                    reply_to_message=None,
                    chat=SimpleNamespace(id=-1001),
                    message_id=10,
                    content_type=content_type,
                )
                bot = SimpleNamespace(
                    copy_message=AsyncMock(
                        return_value=SimpleNamespace(message_id=99)
                    )
                )
                db = SimpleNamespace(
                    find_user_id_by_topic=AsyncMock(return_value=42),
                    log_message_link=AsyncMock(),
                    transaction=transaction,
                )

                await topic_message_to_user(
                    message=message,
                    bot=bot,
                    db=db,
                    log_messages=False,
                )

                copy_kwargs = bot.copy_message.await_args.kwargs
                self.assertEqual(copy_kwargs["message_id"], 10)
                self.assertNotIn("caption", copy_kwargs)
                self.assertNotIn("caption_entities", copy_kwargs)
                self.assertNotIn("parse_mode", copy_kwargs)

    async def test_edited_operator_message_updates_history_and_user_copy(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(is_bot=False),
            message_thread_id=77,
            chat=SimpleNamespace(id=-1001),
            message_id=10,
            content_type="text",
            text="Исправленный ответ",
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
        db = SimpleNamespace(
            find_user_id_by_topic=AsyncMock(return_value=42),
            update_logged_message=AsyncMock(),
        )

        with patch(
            "support_bot.handlers.operator.sync_edited_message",
            new=AsyncMock(return_value=EditSyncStatus.SYNCED),
        ) as sync:
            await edited_topic_message_to_user(
                message=message,
                bot=object(),
                db=db,
                log_messages=True,
            )

        db.update_logged_message.assert_awaited_once_with(
            chat_id=-1001,
            message_id=10,
            content_type="text",
            text="Исправленный ответ",
            caption=None,
            file_id=None,
            payload_json=None,
        )
        sync.assert_awaited_once()
        self.assertEqual(sync.await_args.kwargs["target_chat_id"], 42)

    async def test_edited_operator_message_reports_sync_failure(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(is_bot=False),
            message_thread_id=77,
            chat=SimpleNamespace(id=-1001),
            message_id=10,
            reply=AsyncMock(),
        )
        db = SimpleNamespace(
            find_user_id_by_topic=AsyncMock(return_value=42),
        )

        with patch(
            "support_bot.handlers.operator.sync_edited_message",
            new=AsyncMock(side_effect=MessageEditError("failed")),
        ):
            await edited_topic_message_to_user(
                message=message,
                bot=object(),
                db=db,
                log_messages=False,
            )

        message.reply.assert_awaited_once()

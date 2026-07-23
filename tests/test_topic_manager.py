from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import MessageEntity

from support_bot.topic_manager import MessageDeliveryError, TopicManager


class TopicManagerTests(IsolatedAsyncioTestCase):
    async def test_new_topic_profile_is_russian_and_html_safe(self) -> None:
        db = SimpleNamespace(
            get_active_conversation=AsyncMock(return_value=None),
            set_conversation=AsyncMock(),
        )
        bot = SimpleNamespace(
            create_forum_topic=AsyncMock(
                return_value=SimpleNamespace(message_thread_id=77)
            ),
            send_message=AsyncMock(),
        )
        user = SimpleNamespace(
            id=42,
            username="ivan",
            full_name="Иван <Поддержка>",
        )

        manager = TopicManager(db=db, operator_group_id=-1001)
        topic = await manager.ensure_topic(bot, user)

        self.assertEqual(topic.topic_id, 77)
        profile_text = bot.send_message.await_args.kwargs["text"]
        self.assertIn("Новый диалог.", profile_text)
        self.assertIn("Иван &lt;Поддержка&gt;", profile_text)
        self.assertNotIn("Иван <Поддержка>", profile_text)

    async def test_link_fallback_preserves_entities_without_html_parsing(self) -> None:
        entities = (MessageEntity(type="bold", offset=0, length=6),)
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42),
            message_id=10,
            reply_to_message=None,
            content_type="text",
            text="Жирный https://example.com",
            entities=entities,
        )
        db = SimpleNamespace(
            get_active_conversation=AsyncMock(
                return_value=SimpleNamespace(topic_id=77)
            ),
        )
        bot = SimpleNamespace(
            copy_message=AsyncMock(
                side_effect=TelegramForbiddenError(
                    method=SendMessage(chat_id=1, text="test"),
                    message="forbidden",
                )
            ),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        )

        manager = TopicManager(db=db, operator_group_id=-1001)
        manager._log_message_link = AsyncMock()

        await manager.copy_user_message_to_topic(bot, message)

        send_kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(send_kwargs["text"], message.text)
        self.assertEqual(send_kwargs["entities"], entities)
        self.assertIsNone(send_kwargs["parse_mode"])
        manager._log_message_link.assert_awaited_once()

    async def test_copy_path_supports_text_and_common_media_types(self) -> None:
        content_types = (
            "text",
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
        caption_types = {"animation", "audio", "document", "photo", "video", "voice"}

        for content_type in content_types:
            with self.subTest(content_type=content_type):
                message = SimpleNamespace(
                    from_user=SimpleNamespace(id=42),
                    chat=SimpleNamespace(id=42),
                    message_id=10,
                    reply_to_message=None,
                    quote=None,
                    content_type=content_type,
                    text="Форматированный текст" if content_type == "text" else None,
                    entities=[
                        MessageEntity(type="bold", offset=0, length=16)
                    ]
                    if content_type == "text"
                    else (),
                    caption="Подпись" if content_type in caption_types else None,
                    caption_entities=[
                        MessageEntity(type="italic", offset=0, length=7)
                    ]
                    if content_type in caption_types
                    else (),
                )
                db = SimpleNamespace(
                    get_active_conversation=AsyncMock(
                        return_value=SimpleNamespace(topic_id=77)
                    ),
                )
                bot = SimpleNamespace(
                    copy_message=AsyncMock(
                        return_value=SimpleNamespace(message_id=99)
                    ),
                )
                manager = TopicManager(db=db, operator_group_id=-1001)
                manager._log_message_link = AsyncMock()

                await manager.copy_user_message_to_topic(bot, message)

                copy_kwargs = bot.copy_message.await_args.kwargs
                self.assertEqual(copy_kwargs["message_id"], 10)
                self.assertNotIn("caption", copy_kwargs)
                self.assertNotIn("caption_entities", copy_kwargs)
                self.assertNotIn("parse_mode", copy_kwargs)

    async def test_user_reply_passes_selected_quote_to_operator_topic(self) -> None:
        quote_entities = [MessageEntity(type="bold", offset=0, length=8)]
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42),
            message_id=11,
            reply_to_message=SimpleNamespace(message_id=10),
            quote=SimpleNamespace(
                text="Выделено",
                entities=quote_entities,
                position=4,
            ),
            content_type="text",
            text="Ответ",
            entities=(),
        )
        db = SimpleNamespace(
            get_active_conversation=AsyncMock(
                return_value=SimpleNamespace(topic_id=77)
            ),
            find_linked_message_id=AsyncMock(return_value=98),
        )
        bot = SimpleNamespace(
            copy_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        )
        manager = TopicManager(db=db, operator_group_id=-1001)
        manager._log_message_link = AsyncMock()

        await manager.copy_user_message_to_topic(bot, message)

        reply = bot.copy_message.await_args.kwargs["reply_parameters"]
        self.assertEqual(reply.message_id, 98)
        self.assertEqual(reply.quote, "Выделено")
        self.assertEqual(reply.quote_entities, quote_entities)
        self.assertEqual(reply.quote_position, 4)

    async def test_forbidden_copy_is_reported_as_delivery_error(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42),
            message_id=10,
            reply_to_message=None,
            content_type="photo",
            text=None,
            entities=(),
        )
        db = SimpleNamespace(
            get_active_conversation=AsyncMock(
                return_value=SimpleNamespace(topic_id=77)
            ),
        )
        bot = SimpleNamespace(
            copy_message=AsyncMock(
                side_effect=TelegramForbiddenError(
                    method=SendMessage(chat_id=1, text="test"),
                    message="forbidden",
                )
            ),
        )
        manager = TopicManager(db=db, operator_group_id=-1001)

        with self.assertLogs("support_bot.topic_manager", level="ERROR"):
            with self.assertRaises(MessageDeliveryError):
                await manager.copy_user_message_to_topic(bot, message)

    async def test_missing_topic_is_recreated_and_message_is_retried(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(
                id=42,
                username="ivan",
                full_name="Иван",
            ),
            chat=SimpleNamespace(id=42),
            message_id=10,
            reply_to_message=None,
            content_type="text",
            text="Здравствуйте",
            entities=(),
        )
        db = SimpleNamespace(
            get_active_conversation=AsyncMock(
                side_effect=[SimpleNamespace(topic_id=77), None]
            ),
            deactivate_conversation=AsyncMock(),
            set_conversation=AsyncMock(),
        )
        bot = SimpleNamespace(
            copy_message=AsyncMock(
                side_effect=[
                    TelegramBadRequest(
                        method=SendMessage(chat_id=1, text="test"),
                        message="message thread not found",
                    ),
                    SimpleNamespace(message_id=99),
                ]
            ),
            create_forum_topic=AsyncMock(
                return_value=SimpleNamespace(message_thread_id=88)
            ),
            send_message=AsyncMock(),
        )
        manager = TopicManager(db=db, operator_group_id=-1001)
        manager._log_message_link = AsyncMock()

        topic = await manager.copy_user_message_to_topic(bot, message)

        self.assertEqual(topic.topic_id, 88)
        db.deactivate_conversation.assert_awaited_once_with(42)
        bot.create_forum_topic.assert_awaited_once()
        self.assertEqual(bot.copy_message.await_count, 2)
        manager._log_message_link.assert_awaited_once()

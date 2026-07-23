from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from aiogram.types import MessageEntity

from support_bot.telegram_utils import build_reply_parameters


class ReplyParametersTests(IsolatedAsyncioTestCase):
    async def test_reply_preserves_selected_quote_and_formatting(self) -> None:
        quote_entities = [MessageEntity(type="bold", offset=0, length=8)]
        source_message = SimpleNamespace(
            reply_to_message=SimpleNamespace(message_id=10),
            quote=SimpleNamespace(
                text="Выделено",
                entities=quote_entities,
                position=12,
            ),
        )
        db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=99),
        )

        reply = await build_reply_parameters(
            db,
            source_chat_id=42,
            source_message=source_message,
            target_chat_id=-1001,
        )

        self.assertIsNotNone(reply)
        self.assertEqual(reply.message_id, 99)
        self.assertEqual(reply.quote, "Выделено")
        self.assertEqual(reply.quote_entities, quote_entities)
        self.assertEqual(reply.quote_position, 12)
        self.assertIsNone(reply.quote_parse_mode)

    async def test_reply_without_manual_quote_keeps_message_link(self) -> None:
        source_message = SimpleNamespace(
            reply_to_message=SimpleNamespace(message_id=10),
            quote=None,
        )
        db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=99),
        )

        reply = await build_reply_parameters(
            db,
            source_chat_id=42,
            source_message=source_message,
            target_chat_id=-1001,
        )

        self.assertIsNotNone(reply)
        self.assertEqual(reply.message_id, 99)
        self.assertIsNone(reply.quote)

    async def test_reply_is_omitted_when_original_message_is_not_linked(self) -> None:
        source_message = SimpleNamespace(
            reply_to_message=SimpleNamespace(message_id=10),
            quote=None,
        )
        db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=None),
        )

        reply = await build_reply_parameters(
            db,
            source_chat_id=42,
            source_message=source_message,
            target_chat_id=-1001,
        )

        self.assertIsNone(reply)

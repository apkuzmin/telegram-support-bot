from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from support_bot.handlers.operator import topic_message_to_user


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

        await topic_message_to_user(
            message=message,
            bot=bot,
            db=db,
            log_messages=False,
        )

        bot.copy_message.assert_awaited_once_with(
            chat_id=42,
            from_chat_id=-1001,
            message_id=10,
            reply_parameters=None,
        )

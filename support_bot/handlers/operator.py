from __future__ import annotations

from aiogram import Bot, Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import Message

from support_bot.db import Database


router = Router(name="operator")


def _extract_file_id(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    for attr in ("document", "video", "audio", "voice", "sticker", "animation", "video_note"):
        obj = getattr(message, attr, None)
        if obj is not None:
            return getattr(obj, "file_id", None)
    return None


@router.message(F.is_topic_message == True)  # noqa: E712
async def topic_message_to_user(message: Message, bot: Bot, db: Database) -> None:
    if message.from_user is None:
        return
    if message.from_user.is_bot:
        return

    if message.message_thread_id is None:
        return

    user_id = await db.find_user_id_by_topic(int(message.message_thread_id))
    if user_id is None:
        return

    try:
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except TelegramForbiddenError:
        await message.reply("Пользователь запретил сообщения от бота или не открыл чат с ботом.")
        return
    except TelegramBadRequest as err:
        await message.reply(f"Не удалось отправить пользователю: {getattr(err, 'message', str(err))}")
        return

    await db.log_message(
        user_id=user_id,
        direction="operator",
        chat_id=message.chat.id,
        message_id=message.message_id,
        content_type=message.content_type,
        text=message.text,
        caption=message.caption,
        file_id=_extract_file_id(message),
        payload_json=message.model_dump_json(exclude_none=True, by_alias=True),
    )

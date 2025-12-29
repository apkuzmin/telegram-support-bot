from __future__ import annotations

from aiogram import Bot, Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import Message, ReplyParameters

from support_bot.db import Database
from support_bot.telegram_utils import extract_file_id, safe_payload_json


router = Router(name="operator")


@router.message(F.is_topic_message.is_(True))
async def topic_message_to_user(message: Message, bot: Bot, db: Database, log_messages: bool = True) -> None:
    if message.from_user is None:
        return
    if message.from_user.is_bot:
        return

    if message.message_thread_id is None:
        return

    user_id = await db.find_user_id_by_topic(int(message.message_thread_id))
    if user_id is None:
        return

    reply_params = None
    if message.reply_to_message is not None:
        target_message_id = await db.find_linked_message_id(
            source_chat_id=message.chat.id,
            source_message_id=message.reply_to_message.message_id,
            target_chat_id=user_id,
        )
        if target_message_id is not None:
            reply_params = ReplyParameters(
                message_id=target_message_id,
                allow_sending_without_reply=True,
            )

    try:
        copy_result = await bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_parameters=reply_params,
        )
    except TelegramForbiddenError:
        await message.reply(
            "The user has blocked the bot or has not opened the chat with the bot."
        )
        return
    except TelegramBadRequest as err:
        await message.reply(
            f"Failed to send to the user: {getattr(err, 'message', str(err))}"
        )
        return

    copied_message_id = getattr(copy_result, "message_id", None)
    if copied_message_id is None:
        copied_message_id = int(copy_result)

    async with db.transaction():
        await db.log_message_link(
            user_id=user_id,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            target_chat_id=user_id,
            target_message_id=int(copied_message_id),
            commit=False,
        )
        await db.log_message_link(
            user_id=user_id,
            source_chat_id=user_id,
            source_message_id=int(copied_message_id),
            target_chat_id=message.chat.id,
            target_message_id=message.message_id,
            commit=False,
        )

    if not log_messages:
        return

    await db.log_message(
        user_id=user_id,
        direction="operator",
        chat_id=message.chat.id,
        message_id=message.message_id,
        content_type=message.content_type,
        text=message.text,
        caption=message.caption,
        file_id=extract_file_id(message),
        payload_json=safe_payload_json(message),
    )

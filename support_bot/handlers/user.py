from __future__ import annotations

from aiogram import Bot, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from support_bot.db import Database
from support_bot.telegram_utils import extract_file_id, safe_payload_json
from support_bot.topic_manager import TopicManager


router = Router(name="user")


async def _log_user_message(db: Database, message: Message, *, log_messages: bool) -> None:
    if message.from_user is None:
        return
    if not log_messages:
        return

    await db.log_user_message(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        direction="user",
        chat_id=message.chat.id,
        message_id=message.message_id,
        content_type=message.content_type,
        text=message.text,
        caption=message.caption,
        file_id=extract_file_id(message),
        payload_json=safe_payload_json(message),
    )


@router.message(CommandStart(), F.chat.type == "private")
async def start(
    message: Message, bot: Bot, db: Database, topics: TopicManager, log_messages: bool = True
) -> None:
    if message.from_user is None:
        return

    await _log_user_message(db, message, log_messages=log_messages)
    await topics.copy_user_message_to_topic(bot, message)

    await message.answer("Hello! How can I help you?")


@router.message(F.chat.type == "private")
async def any_private_message(
    message: Message, bot: Bot, db: Database, topics: TopicManager, log_messages: bool = True
) -> None:
    if message.from_user is None:
        return

    await _log_user_message(db, message, log_messages=log_messages)
    await topics.copy_user_message_to_topic(bot, message)

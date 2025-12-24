from __future__ import annotations

from aiogram import Bot, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from support_bot.db import Database
from support_bot.topic_manager import TopicManager


router = Router(name="user")


def _extract_file_id(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    for attr in ("document", "video", "audio", "voice", "sticker", "animation", "video_note"):
        obj = getattr(message, attr, None)
        if obj is not None:
            return getattr(obj, "file_id", None)
    return None


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, bot: Bot, db: Database, topics: TopicManager) -> None:
    if message.from_user is None:
        return

    await db.upsert_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    await db.log_message(
        user_id=message.from_user.id,
        direction="user",
        chat_id=message.chat.id,
        message_id=message.message_id,
        content_type=message.content_type,
        text=message.text,
        caption=message.caption,
        file_id=_extract_file_id(message),
        payload_json=message.model_dump_json(exclude_none=True, by_alias=True),
    )
    await topics.copy_user_message_to_topic(bot, message)

    await message.answer(
        "Здравствуйте! Чем могу вам помочь?"
    )


@router.message(F.chat.type == "private")
async def any_private_message(message: Message, bot: Bot, db: Database, topics: TopicManager) -> None:
    if message.from_user is None:
        return

    await db.upsert_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )

    await db.log_message(
        user_id=message.from_user.id,
        direction="user",
        chat_id=message.chat.id,
        message_id=message.message_id,
        content_type=message.content_type,
        text=message.text,
        caption=message.caption,
        file_id=_extract_file_id(message),
        payload_json=message.model_dump_json(exclude_none=True, by_alias=True),
    )

    await topics.copy_user_message_to_topic(bot, message)

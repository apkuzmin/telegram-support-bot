from __future__ import annotations

from aiogram import Bot, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from support_bot.admin_bridge import AdminSupportBridge
from support_bot.config import DEFAULT_START_MESSAGE
from support_bot.db import Database
from support_bot.message_editor import MessageEditError, sync_edited_message
from support_bot.telegram_utils import extract_file_id, safe_payload_json
from support_bot.topic_manager import MessageDeliveryError, TopicManager, TopicRef


router = Router(name="user")
DELIVERY_ERROR_TEXT = (
    "Не удалось передать сообщение службе поддержки. "
    "Пожалуйста, попробуйте ещё раз немного позже."
)
EDIT_ERROR_TEXT = (
    "Сообщение изменено, но не удалось обновить его копию у службы поддержки. "
    "Пожалуйста, отправьте исправленный текст новым сообщением."
)


async def _store_user_message(db: Database, message: Message, *, log_messages: bool) -> None:
    if message.from_user is None:
        return
    if not log_messages:
        await db.upsert_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
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


async def _copy_to_operator_topic(
    message: Message,
    bot: Bot,
    topics: TopicManager,
) -> TopicRef | None:
    try:
        return await topics.copy_user_message_to_topic(bot, message)
    except MessageDeliveryError:
        await message.answer(DELIVERY_ERROR_TEXT)
        return None


@router.message(CommandStart(), F.chat.type == "private")
async def start(
    message: Message,
    bot: Bot,
    db: Database,
    topics: TopicManager,
    log_messages: bool = True,
    start_message: str = DEFAULT_START_MESSAGE,
    admin_bridge: AdminSupportBridge | None = None,
) -> None:
    if message.from_user is None:
        return

    await _store_user_message(db, message, log_messages=log_messages)
    topic = await _copy_to_operator_topic(message, bot, topics)
    if topic is None:
        return
    if admin_bridge is not None:
        await admin_bridge.publish_user_message(message, topic.topic_id, db)

    await message.answer(start_message)


@router.message(F.chat.type == "private")
async def any_private_message(
    message: Message,
    bot: Bot,
    db: Database,
    topics: TopicManager,
    log_messages: bool = True,
    admin_bridge: AdminSupportBridge | None = None,
) -> None:
    if message.from_user is None:
        return

    await _store_user_message(db, message, log_messages=log_messages)
    topic = await _copy_to_operator_topic(message, bot, topics)
    if topic is not None and admin_bridge is not None:
        await admin_bridge.publish_user_message(message, topic.topic_id, db)


@router.edited_message(F.chat.type == "private")
async def edited_private_message(
    message: Message,
    bot: Bot,
    db: Database,
    topics: TopicManager,
    log_messages: bool = True,
) -> None:
    if message.from_user is None:
        return

    if log_messages:
        await db.update_logged_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            content_type=message.content_type,
            text=message.text,
            caption=message.caption,
            file_id=extract_file_id(message),
            payload_json=safe_payload_json(message),
        )

    try:
        await sync_edited_message(
            bot,
            db,
            source_message=message,
            target_chat_id=topics.operator_group_id,
        )
    except MessageEditError:
        await message.answer(EDIT_ERROR_TEXT)

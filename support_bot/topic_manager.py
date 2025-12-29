from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import LinkPreviewOptions, Message, User

from support_bot.db import Database


@dataclass(frozen=True)
class TopicRef:
    user_id: int
    topic_id: int


def _topic_name(user: User) -> str:
    base = user.full_name or "User"
    if user.username:
        base = f"{base} (@{user.username})"
    name = f"{base} [{user.id}]"
    return name[:128]


def _is_thread_missing(err: TelegramBadRequest) -> bool:
    msg = (getattr(err, "message", None) or "").lower()
    return (
        "message thread not found" in msg
        or "message thread is not found" in msg
        or "thread not found" in msg
        or ("topic" in msg and "closed" in msg)
    )


def _message_has_links(message: Message) -> bool:
    entities = message.entities or ()
    for entity in entities:
        if entity.type in ("url", "text_link"):
            return True
    text = message.text or ""
    return "http://" in text or "https://" in text or "t.me/" in text or "www." in text


class TopicManager:
    def __init__(self, db: Database, operator_group_id: int) -> None:
        self._db = db
        self._operator_group_id = operator_group_id
        self._locks: dict[int, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, user_id: int) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[user_id] = lock
            return lock

    async def ensure_topic(self, bot: Bot, user: User) -> TopicRef:
        lock = await self._lock_for(user.id)
        async with lock:
            existing = await self._db.get_active_conversation(user.id)
            if existing:
                return TopicRef(user_id=user.id, topic_id=existing.topic_id)

            topic = await bot.create_forum_topic(
                chat_id=self._operator_group_id,
                name=_topic_name(user),
            )
            await self._db.set_conversation(user_id=user.id, topic_id=topic.message_thread_id, active=True)

            username_line = f"@{user.username}" if user.username else "—"
            await bot.send_message(
                chat_id=self._operator_group_id,
                message_thread_id=topic.message_thread_id,
                text=(
                    "New conversation.\n"
                    f"User: {user.full_name}\n"
                    f"ID: <code>{user.id}</code>\n"
                    f"Username: {username_line}"
                ),
            )

            return TopicRef(user_id=user.id, topic_id=topic.message_thread_id)

    async def copy_user_message_to_topic(self, bot: Bot, message: Message) -> TopicRef:
        if message.from_user is None:
            raise RuntimeError("Message has no from_user")

        topic = await self.ensure_topic(bot, message.from_user)
        try:
            await bot.copy_message(
                chat_id=self._operator_group_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=topic.topic_id,
            )
            return topic
        except TelegramForbiddenError:
            if message.content_type == "text" and _message_has_links(message):
                try:
                    await bot.send_message(
                        chat_id=self._operator_group_id,
                        message_thread_id=topic.topic_id,
                        text=message.text or "",
                        entities=message.entities,
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                except TelegramForbiddenError:
                    # Bot can't write to the group/topic — nothing else we can do here.
                    pass
                except TelegramBadRequest:
                    pass
            return topic
        except TelegramBadRequest as err:
            if not _is_thread_missing(err):
                try:
                    await bot.send_message(
                        chat_id=self._operator_group_id,
                        message_thread_id=topic.topic_id,
                        text=(
                            "Failed to copy the user's message.\n"
                            f"type={message.content_type}, message_id={message.message_id}\n"
                            f"error={getattr(err, 'message', str(err))}"
                        ),
                    )
                except Exception:
                    pass
                return topic
            await self._db.deactivate_conversation(message.from_user.id)
            topic = await self.ensure_topic(bot, message.from_user)
            await bot.copy_message(
                chat_id=self._operator_group_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=topic.topic_id,
            )
            return topic

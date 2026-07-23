from __future__ import annotations

import asyncio
import io
import logging
from contextlib import suppress
from html import escape
from pathlib import Path
from typing import Any, AsyncIterator

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message, ReplyParameters

from support_bot.omnichannel.enums import Channel, SenderType
from support_bot.omnichannel.files import (
    FileTooLargeError,
    LocalFileStore,
    UnsafeFileTypeError,
)
from support_bot.omnichannel.models import Conversation, Message as StoredMessage, OutboxEvent
from support_bot.omnichannel.service import SupportService
from support_bot.omnichannel.storage import OmnichannelStore
from support_bot.telegram_utils import extract_file_id, safe_payload_json


log = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
STRUCTURED_CONTENT_TYPES = {
    "contact",
    "dice",
    "game",
    "location",
    "poll",
    "venue",
}


def _split_text(value: str, limit: int) -> list[str]:
    if not value:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_units = 0
    for character in value:
        units = len(character.encode("utf-16-le")) // 2
        if current and current_units + units > limit:
            chunks.append("".join(current))
            current = []
            current_units = 0
        current.append(character)
        current_units += units
    if current:
        chunks.append("".join(current))
    return chunks


def _structured_content(message: Message) -> dict[str, Any] | None:
    if message.content_type not in STRUCTURED_CONTENT_TYPES:
        return None
    value = getattr(message, message.content_type, None)
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        data = value.model_dump(mode="json", exclude_none=True)
    else:
        data = value
    return {"type": message.content_type, "data": data}


def _telegram_origin(message: Message) -> str:
    return f"{message.chat.id}:{message.message_id}"


def _file_name(message: Message) -> str:
    if message.document and message.document.file_name:
        return message.document.file_name
    extension = {
        "photo": ".jpg",
        "video": ".mp4",
        "audio": ".mp3",
        "voice": ".ogg",
        "animation": ".gif",
        "video_note": ".mp4",
        "sticker": ".webp",
    }.get(message.content_type, "")
    return f"telegram-{message.message_id}{extension}"


def _file_size(message: Message) -> int | None:
    if message.photo:
        return message.photo[-1].file_size
    for attr in (
        "document",
        "video",
        "audio",
        "voice",
        "sticker",
        "animation",
        "video_note",
    ):
        value = getattr(message, attr, None)
        if value is not None:
            return getattr(value, "file_size", None)
    return None


def _is_thread_missing(error: TelegramBadRequest) -> bool:
    value = str(error).lower()
    return (
        "message thread not found" in value
        or "thread not found" in value
        or ("topic" in value and "closed" in value)
    )


class TelegramBridge:
    def __init__(
        self,
        *,
        bot: Bot,
        store: OmnichannelStore,
        service: SupportService,
        file_store: LocalFileStore,
        operator_group_id: int,
        start_message: str,
    ) -> None:
        self.bot = bot
        self.store = store
        self.service = service
        self.file_store = file_store
        self.operator_group_id = operator_group_id
        self.start_message = start_message
        self._topic_locks: dict[str, asyncio.Lock] = {}
        self._topic_locks_guard = asyncio.Lock()

    async def _topic_lock(self, conversation_id: str) -> asyncio.Lock:
        async with self._topic_locks_guard:
            return self._topic_locks.setdefault(
                conversation_id, asyncio.Lock()
            )

    async def _reply_target(
        self,
        source: Message,
        *,
        channel: Channel,
    ) -> str | None:
        replied = source.reply_to_message
        if replied is None:
            return None
        stored = await self.store.find_message_by_external(
            channel=channel,
            external_chat_id=str(source.chat.id),
            external_message_id=str(replied.message_id),
        )
        return stored.id if stored is not None else None

    async def _archive_attachment(
        self,
        *,
        message: Message,
        customer_id: str,
    ) -> list[dict[str, Any]]:
        file_id = extract_file_id(message)
        if file_id is None:
            return []
        size = _file_size(message)
        if size is not None and size > self.file_store.max_bytes:
            return [
                {
                    "unavailable": True,
                    "reason": "file_too_large",
                    "name": _file_name(message),
                    "telegram_file_id": file_id,
                    "size_bytes": size,
                }
            ]
        buffer = io.BytesIO()
        await self.bot.download(file_id, destination=buffer)
        content = buffer.getvalue()

        async def chunks() -> AsyncIterator[bytes]:
            yield content

        try:
            saved = await self.file_store.save(
                filename=_file_name(message),
                content_type=getattr(
                    getattr(message, message.content_type, None),
                    "mime_type",
                    None,
                ),
                chunks=chunks(),
            )
        except (FileTooLargeError, UnsafeFileTypeError) as exc:
            return [
                {
                    "unavailable": True,
                    "reason": (
                        "file_too_large"
                        if isinstance(exc, FileTooLargeError)
                        else "unsafe_file_type"
                    ),
                    "name": _file_name(message),
                    "telegram_file_id": file_id,
                    "size_bytes": len(content),
                }
            ]
        try:
            stored = await self.store.create_stored_file(
                customer_id=customer_id,
                original_name=saved.original_name,
                content_type=saved.content_type,
                size_bytes=saved.size_bytes,
                sha256=saved.sha256,
                storage_key=saved.storage_key,
            )
        except BaseException:
            self.file_store.delete(saved.storage_key)
            raise
        return [
            {
                "id": stored.id,
                "name": stored.original_name,
                "content_type": stored.content_type,
                "size_bytes": stored.size_bytes,
                "sha256": stored.sha256,
                "telegram_file_id": file_id,
            }
        ]

    async def ingest_customer_message(self, message: Message) -> StoredMessage | None:
        if message.from_user is None:
            return None
        context = await self.service.ensure_telegram_customer(
            telegram_user_id=message.from_user.id,
            display_name=message.from_user.full_name,
            metadata={
                "username": message.from_user.username,
                "first_name": message.from_user.first_name,
                "last_name": message.from_user.last_name,
            },
        )
        attachments = await self._archive_attachment(
            message=message,
            customer_id=context.customer.id,
        )
        reply_to = await self._reply_target(
            message,
            channel=Channel.TELEGRAM_USER,
        )
        stored, _ = await self.service.create_message(
            conversation=context.conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=context.customer.id,
            origin_channel=Channel.TELEGRAM_USER,
            origin_external_id=_telegram_origin(message),
            text=message.text or message.caption,
            reply_to_message_id=reply_to,
            attachments=attachments,
            metadata={
                "telegram_chat_id": str(message.chat.id),
                "telegram_message_id": str(message.message_id),
                "telegram_content_type": message.content_type,
                "telegram_payload": safe_payload_json(message),
                "structured_content": _structured_content(message),
            },
        )
        return stored

    async def ingest_operator_message(self, message: Message) -> StoredMessage | None:
        if (
            message.from_user is None
            or message.from_user.is_bot
            or message.message_thread_id is None
        ):
            return None
        conversation = await self.store.find_conversation_by_topic(
            int(message.message_thread_id)
        )
        if conversation is None:
            return None
        attachments = await self._archive_attachment(
            message=message,
            customer_id=conversation.customer_id,
        )
        reply_to = await self._reply_target(
            message,
            channel=Channel.TELEGRAM_OPERATOR,
        )
        stored, _ = await self.service.create_message(
            conversation=conversation,
            sender_type=SenderType.OPERATOR,
            sender_id=str(message.from_user.id),
            origin_channel=Channel.TELEGRAM_OPERATOR,
            origin_external_id=_telegram_origin(message),
            text=message.text or message.caption,
            reply_to_message_id=reply_to,
            attachments=attachments,
            metadata={
                "telegram_chat_id": str(message.chat.id),
                "telegram_message_id": str(message.message_id),
                "telegram_content_type": message.content_type,
                "telegram_payload": safe_payload_json(message),
                "structured_content": _structured_content(message),
            },
        )
        return stored

    async def ingest_edited_message(
        self,
        message: Message,
        *,
        channel: Channel,
    ) -> StoredMessage | None:
        stored = await self.store.find_message_by_external(
            channel=channel,
            external_chat_id=str(message.chat.id),
            external_message_id=str(message.message_id),
        )
        if stored is None:
            return None
        return await self.store.update_message_text(
            stored.id,
            text_value=message.text or message.caption or "",
        )

    async def ensure_topic(self, conversation: Conversation) -> int:
        if conversation.telegram_topic_id is not None:
            return int(conversation.telegram_topic_id)
        lock = await self._topic_lock(conversation.id)
        async with lock:
            refreshed = await self.store.get_conversation(conversation.id)
            if refreshed is None:
                raise KeyError(conversation.id)
            if refreshed.telegram_topic_id is not None:
                return int(refreshed.telegram_topic_id)
            customer = await self.store.get_customer(conversation.customer_id)
            identity = await self.store.get_identity(
                conversation.customer_id,
                Channel(conversation.customer_channel),
            )
            display_name = customer.display_name if customer else None
            base = display_name or "Пользователь"
            source = (
                "Telegram"
                if conversation.customer_channel == Channel.TELEGRAM_USER.value
                else "Сайт"
            )
            topic = await self.bot.create_forum_topic(
                chat_id=self.operator_group_id,
                name=f"{base} · {source} [{conversation.id[:8]}]"[:128],
            )
            topic_id = int(topic.message_thread_id)
            await self.store.set_telegram_topic(conversation.id, topic_id)
            external_id = identity.external_id if identity else "—"
            await self.bot.send_message(
                chat_id=self.operator_group_id,
                message_thread_id=topic_id,
                text=(
                    "Новый диалог.\n"
                    f"Канал: <b>{source}</b>\n"
                    f"Пользователь: {escape(base)}\n"
                    f"Внешний ID: <code>{escape(external_id)}</code>\n"
                    f"Диалог: <code>{conversation.id}</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
            return topic_id

    async def _reply_parameters(
        self,
        message: StoredMessage,
        *,
        target_channel: Channel,
        target_chat_id: str,
    ) -> ReplyParameters | None:
        if message.reply_to_message_id is None:
            return None
        replied = await self.store.get_message(message.reply_to_message_id)
        if replied is None:
            return None
        if replied.origin_channel == target_channel.value:
            chat_id = str(replied.metadata_json.get("telegram_chat_id", ""))
            message_id = str(
                replied.metadata_json.get("telegram_message_id", "")
            )
            if chat_id == target_chat_id and message_id.isdigit():
                return ReplyParameters(
                    message_id=int(message_id),
                    allow_sending_without_reply=True,
                )
        delivery = await self.store.get_delivery_for_message(
            message_id=replied.id,
            channel=target_channel,
        )
        if (
            delivery is not None
            and delivery.external_chat_id == target_chat_id
            and delivery.external_message_id
        ):
            return ReplyParameters(
                message_id=int(delivery.external_message_id),
                allow_sending_without_reply=True,
            )
        return None

    async def _send_from_web(
        self,
        *,
        message: StoredMessage,
        chat_id: int,
        message_thread_id: int | None,
        reply_parameters: ReplyParameters | None,
    ) -> int:
        attachments = [
            attachment
            for attachment in message.attachments_json
            if attachment.get("id") and not attachment.get("unavailable")
        ]
        if not attachments:
            chunks = _split_text(
                message.text or "(пустое сообщение)",
                TELEGRAM_TEXT_LIMIT,
            )
            first_message_id: int | None = None
            for index, chunk in enumerate(chunks):
                sent = await self.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=chunk,
                    reply_parameters=reply_parameters if index == 0 else None,
                    parse_mode=None,
                )
                first_message_id = first_message_id or sent.message_id
            if first_message_id is None:
                raise RuntimeError("No text could be delivered")
            return first_message_id
        stored_files = await self.store.get_files(
            [str(item["id"]) for item in attachments]
        )
        text_chunks = _split_text(message.text or "", TELEGRAM_CAPTION_LIMIT)
        caption = text_chunks[0] if text_chunks else None
        remaining_text = (message.text or "")[len(caption or "") :]
        first_message_id: int | None = None
        for index, stored in enumerate(stored_files):
            sent = await self.bot.send_document(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                document=FSInputFile(
                    self.file_store.path_for(stored.storage_key),
                    filename=stored.original_name,
                ),
                caption=caption if index == 0 else None,
                reply_parameters=reply_parameters if index == 0 else None,
                parse_mode=None,
            )
            first_message_id = first_message_id or sent.message_id
        if first_message_id is None:
            raise RuntimeError("No attachment could be delivered")
        for chunk in _split_text(remaining_text, TELEGRAM_TEXT_LIMIT):
            await self.bot.send_message(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=chunk,
                parse_mode=None,
            )
        return first_message_id

    async def deliver_to_operator_topic(
        self,
        message: StoredMessage,
        conversation: Conversation,
        *,
        allow_recreate: bool = True,
    ) -> tuple[str, str]:
        topic_id = await self.ensure_topic(conversation)
        reply = await self._reply_parameters(
            message,
            target_channel=Channel.TELEGRAM_OPERATOR,
            target_chat_id=str(self.operator_group_id),
        )
        try:
            if message.origin_channel == Channel.TELEGRAM_USER.value:
                result = await self.bot.copy_message(
                    chat_id=self.operator_group_id,
                    from_chat_id=int(message.metadata_json["telegram_chat_id"]),
                    message_id=int(message.metadata_json["telegram_message_id"]),
                    message_thread_id=topic_id,
                    reply_parameters=reply,
                )
                message_id = int(getattr(result, "message_id", result))
            else:
                message_id = await self._send_from_web(
                    message=message,
                    chat_id=self.operator_group_id,
                    message_thread_id=topic_id,
                    reply_parameters=reply,
                )
        except TelegramBadRequest as exc:
            if not allow_recreate or not _is_thread_missing(exc):
                raise
            await self.store.clear_telegram_topic(conversation.id)
            refreshed = await self.store.get_conversation(conversation.id)
            if refreshed is None:
                raise KeyError(conversation.id) from exc
            return await self.deliver_to_operator_topic(
                message,
                refreshed,
                allow_recreate=False,
            )
        return str(self.operator_group_id), str(message_id)

    async def deliver_to_telegram_customer(
        self, message: StoredMessage, conversation: Conversation
    ) -> tuple[str, str]:
        identity = await self.store.get_identity(
            conversation.customer_id,
            Channel.TELEGRAM_USER,
        )
        if identity is None:
            raise RuntimeError("Telegram customer identity is missing")
        chat_id = int(identity.external_id)
        reply = await self._reply_parameters(
            message,
            target_channel=Channel.TELEGRAM_USER,
            target_chat_id=str(chat_id),
        )
        if message.origin_channel == Channel.TELEGRAM_OPERATOR.value:
            result = await self.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=int(message.metadata_json["telegram_chat_id"]),
                message_id=int(message.metadata_json["telegram_message_id"]),
                reply_parameters=reply,
            )
            message_id = int(getattr(result, "message_id", result))
        else:
            message_id = await self._send_from_web(
                message=message,
                chat_id=chat_id,
                message_thread_id=None,
                reply_parameters=reply,
            )
        return str(chat_id), str(message_id)

    async def process_outbox_event(self, event: OutboxEvent) -> None:
        delivery_id = str(event.payload_json["delivery_id"])
        delivery = await self.store.get_delivery(delivery_id)
        if delivery is None:
            raise KeyError(delivery_id)
        message = await self.store.get_message(delivery.message_id)
        if message is None:
            raise KeyError(delivery.message_id)
        conversation = await self.store.get_conversation(message.conversation_id)
        if conversation is None:
            raise KeyError(message.conversation_id)
        if event.event_type == "message.delivery.edit_requested":
            try:
                if not delivery.external_chat_id or not delivery.external_message_id:
                    raise RuntimeError("Edited delivery has no Telegram location")
                kwargs = {
                    "chat_id": int(delivery.external_chat_id),
                    "message_id": int(delivery.external_message_id),
                }
                if message.attachments_json:
                    await self.bot.edit_message_caption(
                        **kwargs,
                        caption=message.text,
                        parse_mode=None,
                    )
                else:
                    await self.bot.edit_message_text(
                        **kwargs,
                        text=message.text or "",
                        parse_mode=None,
                    )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    await self.store.mark_delivery_failed(
                        event_id=event.id,
                        delivery_id=delivery_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    return
            except Exception as exc:
                await self.store.mark_delivery_failed(
                    event_id=event.id,
                    delivery_id=delivery_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return
            await self.store.mark_delivery_edit_sent(
                event_id=event.id,
                delivery_id=delivery_id,
            )
            return
        try:
            if delivery.channel == Channel.TELEGRAM_OPERATOR.value:
                external_chat_id, external_message_id = (
                    await self.deliver_to_operator_topic(message, conversation)
                )
            elif delivery.channel == Channel.TELEGRAM_USER.value:
                external_chat_id, external_message_id = (
                    await self.deliver_to_telegram_customer(message, conversation)
                )
            elif delivery.channel == Channel.WEB_USER.value:
                external_chat_id, external_message_id = (
                    conversation.id,
                    message.id,
                )
            else:
                raise RuntimeError(
                    f"Unsupported delivery channel: {delivery.channel}"
                )
        except Exception as exc:
            await self.store.mark_delivery_failed(
                event_id=event.id,
                delivery_id=delivery_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            log.exception("Failed to process support delivery %s", delivery_id)
            return
        await self.store.mark_delivery_sent(
            event_id=event.id,
            delivery_id=delivery_id,
            external_chat_id=external_chat_id,
            external_message_id=external_message_id,
        )

    async def run_outbox_once(self) -> int:
        events = await self.store.claim_outbox()
        for event in events:
            await self.process_outbox_event(event)
        return len(events)

    async def run_outbox_forever(
        self,
        *,
        stop_event: asyncio.Event,
        poll_interval: float = 0.5,
    ) -> None:
        while not stop_event.is_set():
            processed = await self.run_outbox_once()
            if processed == 0:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=poll_interval,
                    )


def build_telegram_router(bridge: TelegramBridge) -> Router:
    router = Router(name="omnichannel")

    @router.message(CommandStart(), F.chat.type == "private")
    async def start(message: Message) -> None:
        await bridge.ingest_customer_message(message)
        await message.answer(bridge.start_message, parse_mode=None)

    @router.message(F.chat.type == "private")
    async def private_message(message: Message) -> None:
        await bridge.ingest_customer_message(message)

    @router.message(
        F.chat.id == bridge.operator_group_id,
        F.is_topic_message.is_(True),
    )
    async def operator_message(message: Message) -> None:
        await bridge.ingest_operator_message(message)

    @router.edited_message(F.chat.type == "private")
    async def edited_private_message(message: Message) -> None:
        await bridge.ingest_edited_message(
            message,
            channel=Channel.TELEGRAM_USER,
        )

    @router.edited_message(
        F.chat.id == bridge.operator_group_id,
        F.is_topic_message.is_(True),
    )
    async def edited_operator_message(message: Message) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        await bridge.ingest_edited_message(
            message,
            channel=Channel.TELEGRAM_OPERATOR,
        )

    return router

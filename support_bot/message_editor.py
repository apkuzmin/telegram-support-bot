from __future__ import annotations

import logging
from enum import Enum

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from support_bot.db import Database


log = logging.getLogger(__name__)


class EditSyncStatus(str, Enum):
    SYNCED = "synced"
    NOT_LINKED = "not_linked"
    UNSUPPORTED = "unsupported"


class MessageEditError(RuntimeError):
    """An edited source message could not be synchronized to its copy."""


def _input_media_from_message(
    message: Message,
) -> (
    InputMediaAnimation
    | InputMediaAudio
    | InputMediaDocument
    | InputMediaPhoto
    | InputMediaVideo
    | None
):
    caption_kwargs = {
        "caption": message.caption,
        "parse_mode": None,
        "caption_entities": message.caption_entities,
    }

    if message.photo:
        return InputMediaPhoto(
            media=message.photo[-1].file_id,
            show_caption_above_media=message.show_caption_above_media,
            has_spoiler=message.has_media_spoiler,
            **caption_kwargs,
        )
    if message.video:
        return InputMediaVideo(
            media=message.video.file_id,
            show_caption_above_media=message.show_caption_above_media,
            width=message.video.width,
            height=message.video.height,
            duration=message.video.duration,
            supports_streaming=message.video.supports_streaming,
            has_spoiler=message.has_media_spoiler,
            **caption_kwargs,
        )
    if message.animation:
        return InputMediaAnimation(
            media=message.animation.file_id,
            show_caption_above_media=message.show_caption_above_media,
            width=message.animation.width,
            height=message.animation.height,
            duration=message.animation.duration,
            has_spoiler=message.has_media_spoiler,
            **caption_kwargs,
        )
    if message.audio:
        return InputMediaAudio(
            media=message.audio.file_id,
            duration=message.audio.duration,
            performer=message.audio.performer,
            title=message.audio.title,
            **caption_kwargs,
        )
    if message.document:
        return InputMediaDocument(
            media=message.document.file_id,
            **caption_kwargs,
        )
    return None


async def sync_edited_message(
    bot: Bot,
    db: Database,
    *,
    source_message: Message,
    target_chat_id: int,
) -> EditSyncStatus:
    target_message_id = await db.find_linked_message_id(
        source_chat_id=source_message.chat.id,
        source_message_id=source_message.message_id,
        target_chat_id=target_chat_id,
    )
    if target_message_id is None:
        log.warning(
            "Edited message has no linked copy: source_chat_id=%s "
            "source_message_id=%s target_chat_id=%s",
            source_message.chat.id,
            source_message.message_id,
            target_chat_id,
        )
        return EditSyncStatus.NOT_LINKED

    try:
        if source_message.text is not None:
            await bot.edit_message_text(
                chat_id=target_chat_id,
                message_id=target_message_id,
                text=source_message.text,
                parse_mode=None,
                entities=source_message.entities,
                link_preview_options=source_message.link_preview_options,
            )
            return EditSyncStatus.SYNCED

        media = _input_media_from_message(source_message)
        if media is not None:
            await bot.edit_message_media(
                chat_id=target_chat_id,
                message_id=target_message_id,
                media=media,
            )
            return EditSyncStatus.SYNCED

        if source_message.content_type == "voice":
            await bot.edit_message_caption(
                chat_id=target_chat_id,
                message_id=target_message_id,
                caption=source_message.caption,
                parse_mode=None,
                caption_entities=source_message.caption_entities,
            )
            return EditSyncStatus.SYNCED
    except TelegramBadRequest as err:
        if "message is not modified" in (getattr(err, "message", "") or "").lower():
            return EditSyncStatus.SYNCED
        raise _edit_error(source_message, target_chat_id, err) from err
    except TelegramAPIError as err:
        raise _edit_error(source_message, target_chat_id, err) from err

    log.warning(
        "Edited message type is not supported by Telegram edit methods: "
        "source_chat_id=%s source_message_id=%s content_type=%s",
        source_message.chat.id,
        source_message.message_id,
        source_message.content_type,
    )
    return EditSyncStatus.UNSUPPORTED


def _edit_error(
    source_message: Message,
    target_chat_id: int,
    err: TelegramAPIError,
) -> MessageEditError:
    log.error(
        "Failed to synchronize edited message: source_chat_id=%s "
        "source_message_id=%s target_chat_id=%s content_type=%s",
        source_message.chat.id,
        source_message.message_id,
        target_chat_id,
        source_message.content_type,
        exc_info=(type(err), err, err.__traceback__),
    )
    return MessageEditError(
        f"Unable to synchronize edited message {source_message.message_id}"
    )

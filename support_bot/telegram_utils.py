from __future__ import annotations

from aiogram.types import Message, ReplyParameters

from support_bot.db import Database


def extract_file_id(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    for attr in ("document", "video", "audio", "voice", "sticker", "animation", "video_note"):
        obj = getattr(message, attr, None)
        if obj is not None:
            return getattr(obj, "file_id", None)
    return None


def safe_payload_json(message: Message) -> str | None:
    try:
        return message.model_dump_json(exclude_none=True, by_alias=True, exclude_defaults=True)
    except Exception:
        return None


async def build_reply_parameters(
    db: Database,
    *,
    source_chat_id: int,
    source_message: Message,
    target_chat_id: int,
) -> ReplyParameters | None:
    replied_message = source_message.reply_to_message
    if replied_message is None:
        return None

    target_message_id = await db.find_linked_message_id(
        source_chat_id=source_chat_id,
        source_message_id=replied_message.message_id,
        target_chat_id=target_chat_id,
    )
    if target_message_id is None:
        return None

    quote = source_message.quote
    return ReplyParameters(
        message_id=target_message_id,
        allow_sending_without_reply=True,
        quote=quote.text if quote is not None else None,
        quote_parse_mode=None,
        quote_entities=quote.entities if quote is not None else None,
        quote_position=quote.position if quote is not None else None,
    )

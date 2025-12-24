from __future__ import annotations

from aiogram.types import Message


def extract_file_id(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    for attr in ("document", "video", "audio", "voice", "sticker", "animation", "video_note"):
        obj = getattr(message, attr, None)
        if obj is not None:
            return getattr(obj, "file_id", None)
    return None


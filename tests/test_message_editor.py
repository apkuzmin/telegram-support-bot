from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import (
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    MessageEntity,
)

from support_bot.message_editor import (
    EditSyncStatus,
    MessageEditError,
    sync_edited_message,
)


def edited_message(**overrides):
    values = {
        "chat": SimpleNamespace(id=42),
        "message_id": 10,
        "content_type": "text",
        "text": None,
        "entities": None,
        "link_preview_options": None,
        "caption": None,
        "caption_entities": None,
        "show_caption_above_media": None,
        "has_media_spoiler": None,
        "photo": None,
        "video": None,
        "animation": None,
        "audio": None,
        "document": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MessageEditorTests(IsolatedAsyncioTestCase):
    async def test_text_edit_preserves_entities_and_link_preview(self) -> None:
        entities = [MessageEntity(type="bold", offset=0, length=8)]
        preview = LinkPreviewOptions(is_disabled=True)
        message = edited_message(
            text="Изменено",
            entities=entities,
            link_preview_options=preview,
        )
        db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=99),
        )
        bot = SimpleNamespace(edit_message_text=AsyncMock())

        status = await sync_edited_message(
            bot,
            db,
            source_message=message,
            target_chat_id=-1001,
        )

        self.assertEqual(status, EditSyncStatus.SYNCED)
        bot.edit_message_text.assert_awaited_once_with(
            chat_id=-1001,
            message_id=99,
            text="Изменено",
            parse_mode=None,
            entities=entities,
            link_preview_options=preview,
        )

    async def test_supported_media_edits_reuse_file_id_and_caption(self) -> None:
        caption_entities = [MessageEntity(type="italic", offset=0, length=7)]
        media_cases = (
            (
                "photo",
                {"photo": [SimpleNamespace(file_id="photo-id")]},
                InputMediaPhoto,
                "photo-id",
            ),
            (
                "video",
                {
                    "video": SimpleNamespace(
                        file_id="video-id",
                        width=1280,
                        height=720,
                        duration=10,
                        supports_streaming=True,
                    )
                },
                InputMediaVideo,
                "video-id",
            ),
            (
                "animation",
                {
                    "animation": SimpleNamespace(
                        file_id="animation-id",
                        width=640,
                        height=480,
                        duration=3,
                    )
                },
                InputMediaAnimation,
                "animation-id",
            ),
            (
                "audio",
                {
                    "audio": SimpleNamespace(
                        file_id="audio-id",
                        duration=30,
                        performer="Исполнитель",
                        title="Название",
                    )
                },
                InputMediaAudio,
                "audio-id",
            ),
            (
                "document",
                {"document": SimpleNamespace(file_id="document-id")},
                InputMediaDocument,
                "document-id",
            ),
        )

        for content_type, media_fields, media_class, file_id in media_cases:
            with self.subTest(content_type=content_type):
                message = edited_message(
                    content_type=content_type,
                    caption="Подпись",
                    caption_entities=caption_entities,
                    show_caption_above_media=True,
                    has_media_spoiler=True,
                    **media_fields,
                )
                db = SimpleNamespace(
                    find_linked_message_id=AsyncMock(return_value=99),
                )
                bot = SimpleNamespace(edit_message_media=AsyncMock())

                status = await sync_edited_message(
                    bot,
                    db,
                    source_message=message,
                    target_chat_id=-1001,
                )

                self.assertEqual(status, EditSyncStatus.SYNCED)
                media = bot.edit_message_media.await_args.kwargs["media"]
                self.assertIsInstance(media, media_class)
                self.assertEqual(media.media, file_id)
                self.assertEqual(media.caption, "Подпись")
                self.assertEqual(media.caption_entities, caption_entities)
                self.assertIsNone(media.parse_mode)

    async def test_voice_caption_is_edited_without_replacing_media(self) -> None:
        caption_entities = [MessageEntity(type="spoiler", offset=0, length=7)]
        message = edited_message(
            content_type="voice",
            caption="Подпись",
            caption_entities=caption_entities,
        )
        db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=99),
        )
        bot = SimpleNamespace(edit_message_caption=AsyncMock())

        status = await sync_edited_message(
            bot,
            db,
            source_message=message,
            target_chat_id=-1001,
        )

        self.assertEqual(status, EditSyncStatus.SYNCED)
        bot.edit_message_caption.assert_awaited_once_with(
            chat_id=-1001,
            message_id=99,
            caption="Подпись",
            parse_mode=None,
            caption_entities=caption_entities,
        )

    async def test_unlinked_and_unsupported_edits_are_reported(self) -> None:
        message = edited_message(content_type="sticker")
        unlinked_db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=None),
        )
        unsupported_db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=99),
        )
        bot = SimpleNamespace()

        with self.assertLogs("support_bot.message_editor", level="WARNING"):
            unlinked = await sync_edited_message(
                bot,
                unlinked_db,
                source_message=message,
                target_chat_id=-1001,
            )
            unsupported = await sync_edited_message(
                bot,
                unsupported_db,
                source_message=message,
                target_chat_id=-1001,
            )

        self.assertEqual(unlinked, EditSyncStatus.NOT_LINKED)
        self.assertEqual(unsupported, EditSyncStatus.UNSUPPORTED)

    async def test_api_failure_becomes_message_edit_error(self) -> None:
        message = edited_message(text="Изменено")
        db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=99),
        )
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=SendMessage(chat_id=1, text="test"),
                    message="message can't be edited",
                )
            )
        )

        with self.assertLogs("support_bot.message_editor", level="ERROR"):
            with self.assertRaises(MessageEditError):
                await sync_edited_message(
                    bot,
                    db,
                    source_message=message,
                    target_chat_id=-1001,
                )

    async def test_message_not_modified_is_treated_as_success(self) -> None:
        message = edited_message(text="Изменено")
        db = SimpleNamespace(
            find_linked_message_id=AsyncMock(return_value=99),
        )
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=SendMessage(chat_id=1, text="test"),
                    message="message is not modified",
                )
            )
        )

        status = await sync_edited_message(
            bot,
            db,
            source_message=message,
            target_chat_id=-1001,
        )

        self.assertEqual(status, EditSyncStatus.SYNCED)

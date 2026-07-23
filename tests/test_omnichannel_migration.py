import tempfile
from unittest import IsolatedAsyncioTestCase

from support_bot.db import Database
from support_bot.omnichannel.enums import Channel
from support_bot.omnichannel.migrate_legacy import migrate_legacy
from support_bot.omnichannel.storage import OmnichannelStore


class LegacyMigrationTests(IsolatedAsyncioTestCase):
    async def test_legacy_routing_history_and_links_are_imported(self) -> None:
        with (
            tempfile.NamedTemporaryFile(suffix=".sqlite3") as legacy_file,
            tempfile.NamedTemporaryFile(suffix=".sqlite3") as target_file,
        ):
            legacy = Database(legacy_file.name)
            await legacy.connect()
            try:
                await legacy.init()
                await legacy.log_user_message(
                    user_id=42,
                    username="ivan",
                    first_name="Иван",
                    last_name=None,
                    direction="user",
                    chat_id=42,
                    message_id=10,
                    content_type="text",
                    text="Старое сообщение",
                    caption=None,
                    file_id=None,
                    payload_json='{"text":"Старое сообщение"}',
                )
                await legacy.set_conversation(42, 77)
                async with legacy.transaction():
                    await legacy.log_message_link(
                        user_id=42,
                        source_chat_id=42,
                        source_message_id=10,
                        target_chat_id=-1001,
                        target_message_id=99,
                        commit=False,
                    )
                    await legacy.log_message_link(
                        user_id=42,
                        source_chat_id=-1001,
                        source_message_id=99,
                        target_chat_id=42,
                        target_message_id=10,
                        commit=False,
                    )
            finally:
                await legacy.close()

            target_url = f"sqlite+aiosqlite:///{target_file.name}"
            target = OmnichannelStore(target_url)
            await target.create_schema()
            await target.close()

            result = await migrate_legacy(
                legacy_path=legacy_file.name,
                database_url=target_url,
            )
            self.assertEqual(
                result,
                {
                    "users": 1,
                    "conversations": 1,
                    "messages": 1,
                    "deliveries": 1,
                },
            )

            migrated = OmnichannelStore(target_url)
            try:
                context = await migrated.get_or_create_customer_context(
                    channel=Channel.TELEGRAM_USER,
                    external_id="42",
                    display_name="Иван",
                )
                self.assertEqual(context.conversation.telegram_topic_id, 77)
                messages = await migrated.list_messages(
                    context.conversation.id
                )
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0].text, "Старое сообщение")
                delivery = await migrated.get_delivery_for_message(
                    message_id=messages[0].id,
                    channel=Channel.TELEGRAM_OPERATOR,
                )
                self.assertEqual(delivery.external_message_id, "99")
            finally:
                await migrated.close()

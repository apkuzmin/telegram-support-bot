import datetime as dt
import tempfile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import Chat, Location, Message, User

from support_bot.omnichannel.enums import (
    Channel,
    DeliveryStatus,
    SenderType,
)
from support_bot.omnichannel.files import LocalFileStore
from support_bot.omnichannel.realtime import RealtimeHub
from support_bot.omnichannel.schemas import MessageView
from support_bot.omnichannel.service import SupportService
from support_bot.omnichannel.storage import OmnichannelStore
from support_bot.omnichannel.telegram_bridge import TelegramBridge


class TelegramBridgeTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = OmnichannelStore(
            f"sqlite+aiosqlite:///{self.temp_dir.name}/support.sqlite3"
        )
        await self.store.create_schema()
        self.service = SupportService(self.store, RealtimeHub())
        self.bot = SimpleNamespace(
            create_forum_topic=AsyncMock(
                return_value=SimpleNamespace(message_thread_id=77)
            ),
            send_message=AsyncMock(
                side_effect=[
                    SimpleNamespace(message_id=1),
                    SimpleNamespace(message_id=99),
                ]
            ),
            send_document=AsyncMock(),
            copy_message=AsyncMock(
                return_value=SimpleNamespace(message_id=101)
            ),
            download=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
        )
        self.bridge = TelegramBridge(
            bot=self.bot,
            store=self.store,
            service=self.service,
            file_store=LocalFileStore(
                f"{self.temp_dir.name}/uploads",
                max_bytes=1024 * 1024,
            ),
            operator_group_id=-1001,
            start_message="Здравствуйте!",
        )

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self.temp_dir.cleanup()

    async def test_web_customer_message_is_delivered_to_new_topic(self) -> None:
        session = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        message, _ = await self.service.create_message(
            conversation=session.context.conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=session.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="web-request-1",
            text="Нужна помощь",
        )
        events = await self.store.claim_outbox()
        self.assertEqual(len(events), 1)
        await self.bridge.process_outbox_event(events[0])

        self.bot.create_forum_topic.assert_awaited_once()
        delivery = await self.store.get_delivery(
            str(events[0].payload_json["delivery_id"])
        )
        self.assertEqual(delivery.status, DeliveryStatus.SENT.value)
        self.assertEqual(delivery.external_chat_id, "-1001")
        self.assertEqual(delivery.external_message_id, "99")
        conversation = await self.store.get_conversation(
            message.conversation_id
        )
        self.assertEqual(conversation.telegram_topic_id, 77)

    async def test_telegram_customer_ingress_is_canonical_and_copyable(self) -> None:
        telegram_message = Message(
            message_id=10,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=42, type=ChatType.PRIVATE),
            from_user=User(
                id=42,
                is_bot=False,
                first_name="Иван",
                username="ivan",
            ),
            text="Сообщение из Telegram",
        )
        stored = await self.bridge.ingest_customer_message(telegram_message)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.origin_channel, Channel.TELEGRAM_USER.value)

        events = await self.store.claim_outbox()
        await self.bridge.process_outbox_event(events[0])
        self.bot.copy_message.assert_awaited_once_with(
            chat_id=-1001,
            from_chat_id=42,
            message_id=10,
            message_thread_id=77,
            reply_parameters=None,
        )

        edited_telegram_message = Message(
            message_id=10,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=42, type=ChatType.PRIVATE),
            from_user=User(
                id=42,
                is_bot=False,
                first_name="Иван",
                username="ivan",
            ),
            text="Исправлено в Telegram",
        )
        updated = await self.bridge.ingest_edited_message(
            edited_telegram_message,
            channel=Channel.TELEGRAM_USER,
        )
        self.assertEqual(updated.text, "Исправлено в Telegram")
        edit_events = await self.store.claim_outbox()
        self.assertEqual(len(edit_events), 1)
        await self.bridge.process_outbox_event(edit_events[0])
        self.bot.edit_message_text.assert_awaited_once_with(
            chat_id=-1001,
            message_id=101,
            text="Исправлено в Telegram",
            parse_mode=None,
        )

    async def test_web_text_is_plain_and_split_to_telegram_limits(self) -> None:
        session = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        await self.store.set_telegram_topic(
            session.context.conversation.id,
            77,
        )
        text = "2 < 3 & support\n" + ("😀" * 5000)
        await self.service.create_message(
            conversation=session.context.conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=session.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="web-long-plain-text",
            text=text,
        )
        self.bot.send_message.side_effect = None
        self.bot.send_message.return_value = SimpleNamespace(message_id=300)
        events = await self.store.claim_outbox()
        await self.bridge.process_outbox_event(events[0])

        calls = self.bot.send_message.await_args_list
        self.assertGreater(len(calls), 2)
        delivered = "".join(call.kwargs["text"] for call in calls)
        self.assertEqual(delivered, text)
        self.assertTrue(
            all(
                len(call.kwargs["text"].encode("utf-16-le")) // 2 <= 4096
                for call in calls
            )
        )
        self.assertTrue(
            all(call.kwargs["parse_mode"] is None for call in calls)
        )

    async def test_structured_telegram_message_is_exposed_to_api_model(self) -> None:
        telegram_message = Message(
            message_id=44,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=42, type=ChatType.PRIVATE),
            from_user=User(id=42, is_bot=False, first_name="Иван"),
            location=Location(latitude=55.75, longitude=37.61),
        )
        stored = await self.bridge.ingest_customer_message(telegram_message)
        view = MessageView.model_validate(stored)
        self.assertEqual(view.kind, "structured")
        self.assertEqual(view.structured_content["type"], "location")
        self.assertEqual(
            view.structured_content["data"]["latitude"],
            55.75,
        )

    async def test_web_delivery_is_completed_without_telegram_send(self) -> None:
        session = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        await self.store.set_telegram_topic(
            session.context.conversation.id, 77
        )
        await self.service.create_message(
            conversation=session.context.conversation,
            sender_type=SenderType.OPERATOR,
            sender_id="operator-1",
            origin_channel=Channel.TELEGRAM_OPERATOR,
            origin_external_id="-1001:55",
            text="Ответ из Telegram",
            metadata={
                "telegram_chat_id": "-1001",
                "telegram_message_id": "55",
            },
        )
        events = await self.store.claim_outbox()
        self.assertEqual(len(events), 1)
        await self.bridge.process_outbox_event(events[0])
        delivery = await self.store.get_delivery(
            str(events[0].payload_json["delivery_id"])
        )
        self.assertEqual(delivery.channel, Channel.WEB_USER.value)
        self.assertEqual(delivery.status, DeliveryStatus.SENT.value)
        self.bot.copy_message.assert_not_awaited()

    async def test_telegram_operator_reply_reaches_telegram_customer(self) -> None:
        customer_message = Message(
            message_id=10,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=42, type=ChatType.PRIVATE),
            from_user=User(id=42, is_bot=False, first_name="Иван"),
            text="Вопрос",
        )
        await self.bridge.ingest_customer_message(customer_message)
        customer_events = await self.store.claim_outbox()
        await self.bridge.process_outbox_event(customer_events[0])
        self.bot.copy_message.reset_mock()
        self.bot.copy_message.return_value = SimpleNamespace(message_id=202)

        operator_message = Message(
            message_id=20,
            message_thread_id=77,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=-1001, type=ChatType.SUPERGROUP),
            from_user=User(id=7, is_bot=False, first_name="Оператор"),
            text="Ответ из Telegram",
        )
        stored = await self.bridge.ingest_operator_message(operator_message)
        self.assertIsNotNone(stored)
        operator_events = await self.store.claim_outbox()
        self.assertEqual(len(operator_events), 1)
        await self.bridge.process_outbox_event(operator_events[0])
        self.bot.copy_message.assert_awaited_once_with(
            chat_id=42,
            from_chat_id=-1001,
            message_id=20,
            reply_parameters=None,
        )

    async def test_telegram_operator_reply_reaches_web_customer(self) -> None:
        session = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        await self.store.set_telegram_topic(
            session.context.conversation.id,
            77,
        )
        operator_message = Message(
            message_id=20,
            message_thread_id=77,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=-1001, type=ChatType.SUPERGROUP),
            from_user=User(id=7, is_bot=False, first_name="Оператор"),
            text="Ответ посетителю сайта",
        )
        stored = await self.bridge.ingest_operator_message(operator_message)
        self.assertIsNotNone(stored)
        events = await self.store.claim_outbox()
        self.assertEqual(len(events), 1)
        await self.bridge.process_outbox_event(events[0])
        delivery = await self.store.get_delivery(
            str(events[0].payload_json["delivery_id"])
        )
        self.assertEqual(delivery.channel, Channel.WEB_USER.value)
        self.assertEqual(delivery.status, DeliveryStatus.SENT.value)

    async def test_web_operator_reply_reaches_topic_and_telegram_customer(self) -> None:
        context = await self.service.ensure_telegram_customer(
            telegram_user_id=42,
            display_name="Иван",
        )
        await self.store.set_telegram_topic(context.conversation.id, 77)
        await self.service.create_message(
            conversation=context.conversation,
            sender_type=SenderType.OPERATOR,
            sender_id="web-operator",
            origin_channel=Channel.WEB_OPERATOR,
            origin_external_id="web-operator-request-1",
            text="Ответ с сайта",
        )
        events = await self.store.claim_outbox()
        self.assertEqual(len(events), 2)
        for event in events:
            await self.bridge.process_outbox_event(event)
        calls = self.bot.send_message.await_args_list
        self.assertEqual(
            {call.kwargs["chat_id"] for call in calls},
            {-1001, 42},
        )

    async def test_missing_operator_topic_is_recreated_once(self) -> None:
        session = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        conversation = session.context.conversation
        await self.store.set_telegram_topic(conversation.id, 77)
        await self.service.create_message(
            conversation=conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=session.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="web-request-recreate",
            text="Нужна новая тема",
        )
        self.bot.create_forum_topic.return_value = SimpleNamespace(
            message_thread_id=88
        )
        self.bot.send_message.side_effect = [
            TelegramBadRequest(
                method=SendMessage(chat_id=-1001, text="x"),
                message="message thread not found",
            ),
            SimpleNamespace(message_id=1),
            SimpleNamespace(message_id=109),
        ]
        events = await self.store.claim_outbox()
        await self.bridge.process_outbox_event(events[0])

        refreshed = await self.store.get_conversation(conversation.id)
        self.assertEqual(refreshed.telegram_topic_id, 88)
        delivery = await self.store.get_delivery(
            str(events[0].payload_json["delivery_id"])
        )
        self.assertEqual(delivery.status, DeliveryStatus.SENT.value)
        self.assertEqual(delivery.external_message_id, "109")

    async def test_edit_failure_is_visible_and_manual_retry_repeats_edit(
        self,
    ) -> None:
        session = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        await self.store.set_telegram_topic(
            session.context.conversation.id,
            77,
        )
        message, _ = await self.service.create_message(
            conversation=session.context.conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=session.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="web-edit-failure",
            text="До правки",
        )
        self.bot.send_message.side_effect = None
        self.bot.send_message.return_value = SimpleNamespace(message_id=300)
        delivery_event = (await self.store.claim_outbox())[0]
        await self.bridge.process_outbox_event(delivery_event)
        delivery_id = str(delivery_event.payload_json["delivery_id"])

        await self.store.update_message_text(
            message.id,
            text_value="После правки",
        )
        edit_event = (await self.store.claim_outbox())[0]
        self.bot.edit_message_text.side_effect = TelegramBadRequest(
            method=SendMessage(chat_id=-1001, text="x"),
            message="message cannot be edited",
        )
        await self.bridge.process_outbox_event(edit_event)
        failed = await self.store.get_delivery(delivery_id)
        self.assertEqual(failed.status, DeliveryStatus.FAILED.value)
        self.assertIn("cannot be edited", failed.last_error)

        await self.store.retry_delivery(delivery_id)
        retry_event = (await self.store.claim_outbox())[0]
        self.assertEqual(
            retry_event.event_type,
            "message.delivery.edit_requested",
        )
        self.bot.edit_message_text.side_effect = None
        self.bot.edit_message_text.return_value = True
        await self.bridge.process_outbox_event(retry_event)
        sent = await self.store.get_delivery(delivery_id)
        self.assertEqual(sent.status, DeliveryStatus.SENT.value)
        self.assertIsNone(sent.last_error)

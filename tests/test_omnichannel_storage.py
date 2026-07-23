import tempfile
import datetime as dt
from unittest import IsolatedAsyncioTestCase

from sqlalchemy import func, select

from support_bot.omnichannel.enums import (
    Channel,
    DeliveryStatus,
    SenderType,
)
from support_bot.omnichannel.models import MessageDelivery, OutboxEvent
from support_bot.omnichannel.realtime import RealtimeHub
from support_bot.omnichannel.service import SupportService
from support_bot.omnichannel.storage import OmnichannelStore


class OmnichannelStorageTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3")
        self.store = OmnichannelStore(
            f"sqlite+aiosqlite:///{self.tmp.name}"
        )
        await self.store.create_schema()
        self.service = SupportService(self.store, RealtimeHub())

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self.tmp.close()

    async def test_web_message_is_idempotent_and_gets_telegram_delivery(self) -> None:
        created = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        conversation = created.context.conversation

        first, first_created = await self.service.create_message(
            conversation=conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=created.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="request-123",
            text="Нужна помощь",
        )
        duplicate, duplicate_created = await self.service.create_message(
            conversation=conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=created.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="request-123",
            text="Не должно дублироваться",
        )

        self.assertTrue(first_created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(first.sequence, 1)

        async with self.store.sessions() as session:
            deliveries = await session.scalar(
                select(func.count()).select_from(MessageDelivery)
            )
            outbox = await session.scalar(
                select(func.count()).select_from(OutboxEvent)
            )
        self.assertEqual(deliveries, 1)
        self.assertEqual(outbox, 1)

    async def test_operator_reply_fans_out_to_topic_and_web_customer(self) -> None:
        created = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        conversation = created.context.conversation
        message, was_created = await self.service.create_message(
            conversation=conversation,
            sender_type=SenderType.OPERATOR,
            sender_id="operator-1",
            origin_channel=Channel.WEB_OPERATOR,
            origin_external_id="operator-request-1",
            text="Ответ поддержки",
        )
        self.assertTrue(was_created)
        self.assertEqual(message.sequence, 1)

        async with self.store.sessions() as session:
            channels = list(
                (
                    await session.scalars(
                        select(MessageDelivery.channel).order_by(
                            MessageDelivery.channel
                        )
                    )
                ).all()
            )
        self.assertEqual(
            channels,
            sorted(
                [
                    Channel.TELEGRAM_OPERATOR.value,
                    Channel.WEB_USER.value,
                ]
            ),
        )

    async def test_failed_delivery_is_retried_and_then_marked_sent(self) -> None:
        created = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        await self.service.create_message(
            conversation=created.context.conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=created.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="request-123",
            text="Нужна помощь",
        )
        events = await self.store.claim_outbox()
        self.assertEqual(len(events), 1)
        delivery_id = str(events[0].payload_json["delivery_id"])
        await self.store.mark_delivery_failed(
            event_id=events[0].id,
            delivery_id=delivery_id,
            error="temporary",
        )
        delivery = await self.store.get_delivery(delivery_id)
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.status, DeliveryStatus.FAILED.value)
        self.assertEqual(delivery.attempts, 1)

        async with self.store.sessions.begin() as session:
            event = await session.get(OutboxEvent, events[0].id)
            event.available_at = event.created_at
        retry = await self.store.claim_outbox()
        self.assertEqual(len(retry), 1)
        await self.store.mark_delivery_sent(
            event_id=retry[0].id,
            delivery_id=delivery_id,
            external_chat_id="-1001",
            external_message_id="99",
        )
        delivery = await self.store.get_delivery(delivery_id)
        self.assertEqual(delivery.status, DeliveryStatus.SENT.value)
        self.assertEqual(delivery.external_message_id, "99")

    async def test_abandoned_processing_event_is_reclaimed(self) -> None:
        created = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        await self.service.create_message(
            conversation=created.context.conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=created.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="request-abandoned",
            text="Нужна помощь",
        )
        claimed = await self.store.claim_outbox()
        self.assertEqual(len(claimed), 1)
        async with self.store.sessions.begin() as session:
            event = await session.get(OutboxEvent, claimed[0].id)
            event.locked_at = event.created_at - dt.timedelta(minutes=5)
        reclaimed = await self.store.claim_outbox()
        self.assertEqual([event.id for event in reclaimed], [claimed[0].id])
        self.assertEqual(reclaimed[0].attempts, 2)

    async def test_manual_retry_supersedes_old_outbox_event(self) -> None:
        created = await self.service.create_web_session(
            external_user_id="site:42",
            display_name="Иван",
        )
        await self.service.create_message(
            conversation=created.context.conversation,
            sender_type=SenderType.CUSTOMER,
            sender_id=created.context.customer.id,
            origin_channel=Channel.WEB_USER,
            origin_external_id="request-manual-retry",
            text="Нужна помощь",
        )
        claimed = await self.store.claim_outbox()
        delivery_id = str(claimed[0].payload_json["delivery_id"])
        await self.store.mark_delivery_failed(
            event_id=claimed[0].id,
            delivery_id=delivery_id,
            error="temporary",
        )

        delivery = await self.store.retry_delivery(delivery_id)
        self.assertEqual(delivery.status, DeliveryStatus.PENDING.value)
        retry = await self.store.claim_outbox()
        self.assertEqual(len(retry), 1)
        self.assertNotEqual(retry[0].id, claimed[0].id)
        self.assertEqual(
            retry[0].payload_json["delivery_id"],
            delivery_id,
        )
        async with self.store.sessions() as session:
            old_event = await session.get(OutboxEvent, claimed[0].id)
        self.assertEqual(old_event.status, DeliveryStatus.DEAD.value)
        self.assertEqual(
            old_event.last_error,
            "Superseded by manual retry",
        )

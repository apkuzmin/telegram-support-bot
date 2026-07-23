from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from support_bot.omnichannel.enums import (
    Channel,
    ConversationStatus,
    DeliveryStatus,
    MessageKind,
    SenderType,
)
from support_bot.omnichannel.models import (
    Base,
    ChannelIdentity,
    Conversation,
    ConversationRead,
    Customer,
    Message,
    MessageDelivery,
    OutboxEvent,
    RealtimeEvent,
    StoredFile,
    utcnow,
)


@dataclass(frozen=True)
class DeliveryTarget:
    channel: Channel
    target: str


@dataclass(frozen=True)
class CustomerContext:
    customer: Customer
    identity: ChannelIdentity
    conversation: Conversation


class OmnichannelStore:
    def __init__(
        self,
        database_url: str,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.engine = engine or create_async_engine(
            database_url,
            pool_pre_ping=True,
        )
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        await self.engine.dispose()

    async def create_schema(self) -> None:
        """Development/tests only. Production uses Alembic migrations."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def get_or_create_customer_context(
        self,
        *,
        channel: Channel,
        external_id: str,
        display_name: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> CustomerContext:
        try:
            return await self._get_or_create_customer_context_once(
                channel=channel,
                external_id=external_id,
                display_name=display_name,
                metadata=metadata,
            )
        except IntegrityError:
            # A concurrent first request may win the unique identity insert.
            # Its transaction also creates the conversation, so retry by key.
            return await self._get_or_create_customer_context_once(
                channel=channel,
                external_id=external_id,
                display_name=display_name,
                metadata=metadata,
            )

    async def _get_or_create_customer_context_once(
        self,
        *,
        channel: Channel,
        external_id: str,
        display_name: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> CustomerContext:
        async with self.sessions.begin() as session:
            identity = await session.scalar(
                select(ChannelIdentity).where(
                    ChannelIdentity.channel == channel.value,
                    ChannelIdentity.external_id == external_id,
                )
            )
            if identity is None:
                customer = Customer(display_name=display_name)
                session.add(customer)
                await session.flush()
                identity = ChannelIdentity(
                    customer_id=customer.id,
                    channel=channel.value,
                    external_id=external_id,
                    metadata_json=metadata or {},
                )
                session.add(identity)
                await session.flush()
            else:
                customer = await session.scalar(
                    select(Customer)
                    .where(Customer.id == identity.customer_id)
                    .with_for_update()
                )
                if customer is None:
                    raise RuntimeError("Identity references a missing customer")
                if display_name and customer.display_name != display_name:
                    customer.display_name = display_name
                if metadata:
                    identity.metadata_json = {**identity.metadata_json, **metadata}

            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.customer_id == customer.id,
                    Conversation.customer_channel == channel.value,
                    Conversation.status != ConversationStatus.CLOSED.value,
                )
                .order_by(Conversation.created_at.desc())
                .limit(1)
            )
            if conversation is None:
                conversation = Conversation(
                    customer_id=customer.id,
                    customer_channel=channel.value,
                )
                session.add(conversation)
                await session.flush()
            return CustomerContext(customer, identity, conversation)

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        async with self.sessions() as session:
            return await session.get(Conversation, conversation_id)

    async def get_customer(self, customer_id: str) -> Customer | None:
        async with self.sessions() as session:
            return await session.get(Customer, customer_id)

    async def get_customers(
        self, customer_ids: Iterable[str]
    ) -> list[Customer]:
        ids = list(dict.fromkeys(customer_ids))
        if not ids:
            return []
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(Customer).where(Customer.id.in_(ids))
                    )
                ).all()
            )

    async def get_identity(
        self, customer_id: str, channel: Channel
    ) -> ChannelIdentity | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(ChannelIdentity).where(
                    ChannelIdentity.customer_id == customer_id,
                    ChannelIdentity.channel == channel.value,
                )
            )

    async def list_conversations(
        self,
        *,
        status: ConversationStatus | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Conversation]:
        statement: Select[tuple[Conversation]] = select(Conversation)
        if status is not None:
            statement = statement.where(Conversation.status == status.value)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.join(
                Customer, Customer.id == Conversation.customer_id
            ).where(
                or_(
                    Customer.display_name.ilike(pattern),
                    Conversation.id.ilike(pattern),
                )
            )
        statement = (
            statement.order_by(
                Conversation.updated_at.desc(),
                Conversation.id,
            )
            .offset(offset)
            .limit(limit)
        )
        async with self.sessions() as session:
            return list((await session.scalars(statement)).all())

    async def update_conversation(
        self,
        conversation_id: str,
        *,
        status: ConversationStatus | None = None,
        assigned_operator_id: str | None = None,
        update_assignment: bool = False,
    ) -> Conversation | None:
        async with self.sessions.begin() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                return None
            if status is not None:
                conversation.status = status.value
                conversation.closed_at = (
                    utcnow() if status is ConversationStatus.CLOSED else None
                )
            if update_assignment:
                conversation.assigned_operator_id = assigned_operator_id
            conversation.updated_at = utcnow()
            session.add(
                RealtimeEvent(
                    topics_json=["operators", f"conversation:{conversation.id}"],
                    payload_json={
                        "type": "conversation.updated",
                        "conversation_id": conversation.id,
                    },
                )
            )
            return conversation

    async def set_telegram_topic(
        self, conversation_id: str, topic_id: int
    ) -> None:
        async with self.sessions.begin() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            conversation.telegram_topic_id = topic_id
            conversation.updated_at = utcnow()

    async def clear_telegram_topic(self, conversation_id: str) -> None:
        async with self.sessions.begin() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            conversation.telegram_topic_id = None
            conversation.updated_at = utcnow()

    async def find_conversation_by_topic(
        self, topic_id: int
    ) -> Conversation | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(Conversation).where(
                    Conversation.telegram_topic_id == topic_id,
                    Conversation.status != ConversationStatus.CLOSED.value,
                )
            )

    async def create_message(
        self,
        *,
        conversation_id: str,
        sender_type: SenderType,
        sender_id: str | None,
        origin_channel: Channel,
        origin_external_id: str,
        text: str | None,
        kind: MessageKind = MessageKind.TEXT,
        reply_to_message_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        deliveries: Iterable[DeliveryTarget] = (),
        emit_realtime: bool = True,
    ) -> tuple[Message, bool]:
        async with self.sessions.begin() as session:
            conversation = await session.scalar(
                select(Conversation)
                .where(Conversation.id == conversation_id)
                .with_for_update()
            )
            if conversation is None:
                raise KeyError(conversation_id)
            existing = await session.scalar(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.origin_channel == origin_channel.value,
                    Message.origin_external_id == origin_external_id,
                )
            )
            if existing is not None:
                return existing, False
            if reply_to_message_id is not None:
                replied = await session.get(Message, reply_to_message_id)
                if replied is None or replied.conversation_id != conversation_id:
                    raise ValueError("Reply target is outside the conversation")
            sequence = conversation.next_sequence
            conversation.next_sequence += 1
            conversation.updated_at = utcnow()
            if conversation.status == ConversationStatus.NEW.value:
                conversation.status = ConversationStatus.OPEN.value

            message = Message(
                conversation_id=conversation_id,
                sequence=sequence,
                sender_type=sender_type.value,
                sender_id=sender_id,
                origin_channel=origin_channel.value,
                origin_external_id=origin_external_id,
                kind=kind.value,
                text=text,
                reply_to_message_id=reply_to_message_id,
                attachments_json=attachments or [],
                metadata_json=metadata or {},
            )
            session.add(message)
            await session.flush()

            for target in deliveries:
                delivery = MessageDelivery(
                    message_id=message.id,
                    channel=target.channel.value,
                    target=target.target,
                )
                session.add(delivery)
                await session.flush()
                session.add(
                    OutboxEvent(
                        event_type="message.delivery.requested",
                        aggregate_id=message.id,
                        payload_json={"delivery_id": delivery.id},
                    )
                )
            if emit_realtime:
                session.add(
                    RealtimeEvent(
                        topics_json=[
                            "operators",
                            f"conversation:{conversation.id}",
                            f"customer:{conversation.customer_id}",
                        ],
                        payload_json={
                            "type": "message.created",
                            "conversation_id": conversation.id,
                            "message_id": message.id,
                            "sequence": message.sequence,
                        },
                    )
                )
            return message, True

    async def import_sent_delivery(
        self,
        *,
        message_id: str,
        channel: Channel,
        target: str,
        external_chat_id: str,
        external_message_id: str,
    ) -> MessageDelivery:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(MessageDelivery).where(
                    MessageDelivery.message_id == message_id,
                    MessageDelivery.channel == channel.value,
                    MessageDelivery.target == target,
                )
            )
            if existing is not None:
                return existing
            delivery = MessageDelivery(
                message_id=message_id,
                channel=channel.value,
                target=target,
                status=DeliveryStatus.SENT.value,
                external_chat_id=external_chat_id,
                external_message_id=external_message_id,
                sent_at=utcnow(),
            )
            session.add(delivery)
            await session.flush()
            return delivery

    async def create_stored_file(
        self,
        *,
        customer_id: str,
        original_name: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        storage_key: str,
    ) -> StoredFile:
        async with self.sessions.begin() as session:
            stored = StoredFile(
                customer_id=customer_id,
                original_name=original_name,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
                storage_key=storage_key,
            )
            session.add(stored)
            await session.flush()
            return stored

    async def get_files(
        self, file_ids: Iterable[str], *, customer_id: str | None = None
    ) -> list[StoredFile]:
        ids = list(dict.fromkeys(file_ids))
        if not ids:
            return []
        statement = select(StoredFile).where(StoredFile.id.in_(ids))
        if customer_id is not None:
            statement = statement.where(StoredFile.customer_id == customer_id)
        async with self.sessions() as session:
            return list((await session.scalars(statement)).all())

    async def list_messages(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[Message]:
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(Message)
                        .where(
                            Message.conversation_id == conversation_id,
                            Message.sequence > after_sequence,
                        )
                        .order_by(Message.sequence)
                        .limit(limit)
                    )
                ).all()
            )

    async def get_message(self, message_id: str) -> Message | None:
        async with self.sessions() as session:
            return await session.get(Message, message_id)

    async def update_message_text(
        self,
        message_id: str,
        *,
        text_value: str,
    ) -> Message | None:
        async with self.sessions.begin() as session:
            message = await session.get(Message, message_id)
            if message is None:
                return None
            message.text = text_value
            message.edited_at = utcnow()
            deliveries = list(
                (
                    await session.scalars(
                        select(MessageDelivery).where(
                            MessageDelivery.message_id == message.id,
                            MessageDelivery.status
                            == DeliveryStatus.SENT.value,
                            MessageDelivery.channel.in_(
                                [
                                    Channel.TELEGRAM_USER.value,
                                    Channel.TELEGRAM_OPERATOR.value,
                                ]
                            ),
                        )
                    )
                ).all()
            )
            for delivery in deliveries:
                session.add(
                    OutboxEvent(
                        event_type="message.delivery.edit_requested",
                        aggregate_id=message.id,
                        payload_json={"delivery_id": delivery.id},
                    )
                )
            conversation = await session.get(
                Conversation, message.conversation_id
            )
            customer_id = (
                conversation.customer_id if conversation is not None else ""
            )
            session.add(
                RealtimeEvent(
                    topics_json=[
                        "operators",
                        f"conversation:{message.conversation_id}",
                        f"customer:{customer_id}",
                    ],
                    payload_json={
                        "type": "message.updated",
                        "conversation_id": message.conversation_id,
                        "message_id": message.id,
                        "sequence": message.sequence,
                    },
                )
            )
            return message

    async def find_message_by_external(
        self,
        *,
        channel: Channel,
        external_chat_id: str,
        external_message_id: str,
    ) -> Message | None:
        origin_key = f"{external_chat_id}:{external_message_id}"
        async with self.sessions() as session:
            originated = await session.scalar(
                select(Message).where(
                    Message.origin_channel == channel.value,
                    Message.origin_external_id == origin_key,
                )
            )
            if originated is not None:
                return originated
            return await session.scalar(
                select(Message)
                .join(
                    MessageDelivery,
                    MessageDelivery.message_id == Message.id,
                )
                .where(
                    MessageDelivery.channel == channel.value,
                    MessageDelivery.external_chat_id == external_chat_id,
                    MessageDelivery.external_message_id == external_message_id,
                )
            )

    async def get_delivery_for_message(
        self,
        *,
        message_id: str,
        channel: Channel,
        target: str | None = None,
    ) -> MessageDelivery | None:
        statement = select(MessageDelivery).where(
            MessageDelivery.message_id == message_id,
            MessageDelivery.channel == channel.value,
            MessageDelivery.status == DeliveryStatus.SENT.value,
        )
        if target is not None:
            statement = statement.where(MessageDelivery.target == target)
        async with self.sessions() as session:
            return await session.scalar(statement.limit(1))

    async def list_deliveries_for_messages(
        self, message_ids: Iterable[str]
    ) -> list[MessageDelivery]:
        ids = list(dict.fromkeys(message_ids))
        if not ids:
            return []
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(MessageDelivery)
                        .where(MessageDelivery.message_id.in_(ids))
                        .order_by(MessageDelivery.created_at)
                    )
                ).all()
            )

    async def get_delivery(
        self, delivery_id: str
    ) -> MessageDelivery | None:
        async with self.sessions() as session:
            return await session.get(MessageDelivery, delivery_id)

    async def latest_realtime_event_id(self) -> int:
        async with self.sessions() as session:
            value = await session.scalar(
                select(RealtimeEvent.id)
                .order_by(RealtimeEvent.id.desc())
                .limit(1)
            )
            return int(value or 0)

    async def list_realtime_events(
        self,
        *,
        after_id: int,
        limit: int = 100,
    ) -> list[RealtimeEvent]:
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(RealtimeEvent)
                        .where(RealtimeEvent.id > after_id)
                        .order_by(RealtimeEvent.id)
                        .limit(limit)
                    )
                ).all()
            )

    async def mark_read(
        self, conversation_id: str, actor_key: str, last_sequence: int
    ) -> None:
        async with self.sessions.begin() as session:
            changed = False
            read = await session.get(
                ConversationRead,
                {"conversation_id": conversation_id, "actor_key": actor_key},
            )
            if read is None:
                session.add(
                    ConversationRead(
                        conversation_id=conversation_id,
                        actor_key=actor_key,
                        last_sequence=last_sequence,
                    )
                )
                changed = True
            elif last_sequence > read.last_sequence:
                read.last_sequence = last_sequence
                read.updated_at = utcnow()
                changed = True
            if changed:
                conversation = await session.get(
                    Conversation, conversation_id
                )
                if conversation is None:
                    raise KeyError(conversation_id)
                session.add(
                    RealtimeEvent(
                        topics_json=[
                            "operators",
                            f"conversation:{conversation_id}",
                            f"customer:{conversation.customer_id}",
                        ],
                        payload_json={
                            "type": "conversation.read",
                            "conversation_id": conversation_id,
                            "actor": actor_key,
                            "last_sequence": last_sequence,
                        },
                    )
                )

    async def get_read_state(self, conversation_id: str) -> tuple[int, int]:
        async with self.sessions() as session:
            reads = list(
                (
                    await session.scalars(
                        select(ConversationRead).where(
                            ConversationRead.conversation_id
                            == conversation_id
                        )
                    )
                ).all()
            )
        customer = max(
            (
                item.last_sequence
                for item in reads
                if item.actor_key.startswith("customer:")
            ),
            default=0,
        )
        operator = max(
            (
                item.last_sequence
                for item in reads
                if item.actor_key.startswith("operator:")
            ),
            default=0,
        )
        return customer, operator

    async def claim_outbox(self, *, limit: int = 50) -> list[OutboxEvent]:
        now = utcnow()
        stale_before = now - dt.timedelta(seconds=60)
        async with self.sessions.begin() as session:
            statement = (
                select(OutboxEvent)
                .where(
                    or_(
                        and_(
                            OutboxEvent.status.in_(
                                [
                                    DeliveryStatus.PENDING.value,
                                    DeliveryStatus.FAILED.value,
                                ]
                            ),
                            OutboxEvent.available_at <= now,
                        ),
                        and_(
                            OutboxEvent.status
                            == DeliveryStatus.PROCESSING.value,
                            OutboxEvent.locked_at.is_not(None),
                            OutboxEvent.locked_at <= stale_before,
                        ),
                    ),
                )
                .order_by(OutboxEvent.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            events = list((await session.scalars(statement)).all())
            for event in events:
                event.status = DeliveryStatus.PROCESSING.value
                event.attempts += 1
                event.locked_at = now
            return events

    async def mark_delivery_sent(
        self,
        *,
        event_id: str,
        delivery_id: str,
        external_chat_id: str | None = None,
        external_message_id: str | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            event = await session.get(OutboxEvent, event_id)
            delivery = await session.get(MessageDelivery, delivery_id)
            if event is None or delivery is None:
                raise KeyError(event_id if event is None else delivery_id)
            event.status = DeliveryStatus.SENT.value
            event.locked_at = None
            delivery.status = DeliveryStatus.SENT.value
            delivery.attempts = event.attempts
            delivery.external_chat_id = external_chat_id
            delivery.external_message_id = external_message_id
            delivery.sent_at = utcnow()

    async def mark_delivery_failed(
        self,
        *,
        event_id: str,
        delivery_id: str,
        error: str,
        max_attempts: int = 8,
    ) -> None:
        async with self.sessions.begin() as session:
            event = await session.get(OutboxEvent, event_id)
            delivery = await session.get(MessageDelivery, delivery_id)
            if event is None or delivery is None:
                raise KeyError(event_id if event is None else delivery_id)
            delay_seconds = min(300, 2 ** min(event.attempts, 8))
            dead = event.attempts >= max_attempts
            next_status = (
                DeliveryStatus.DEAD.value if dead else DeliveryStatus.FAILED.value
            )
            event.status = next_status
            event.last_error = error[:4000]
            event.locked_at = None
            event.available_at = utcnow() + dt.timedelta(seconds=delay_seconds)
            delivery.status = next_status
            delivery.attempts = event.attempts
            delivery.next_attempt_at = event.available_at
            delivery.last_error = error[:4000]

    async def mark_outbox_event_sent(self, event_id: str) -> None:
        async with self.sessions.begin() as session:
            event = await session.get(OutboxEvent, event_id)
            if event is None:
                raise KeyError(event_id)
            event.status = DeliveryStatus.SENT.value
            event.locked_at = None

    async def mark_outbox_event_failed(
        self,
        event_id: str,
        *,
        error: str,
        max_attempts: int = 8,
    ) -> None:
        async with self.sessions.begin() as session:
            event = await session.get(OutboxEvent, event_id)
            if event is None:
                raise KeyError(event_id)
            delay_seconds = min(300, 2 ** min(event.attempts, 8))
            event.status = (
                DeliveryStatus.DEAD.value
                if event.attempts >= max_attempts
                else DeliveryStatus.FAILED.value
            )
            event.last_error = error[:4000]
            event.locked_at = None
            event.available_at = utcnow() + dt.timedelta(seconds=delay_seconds)

    async def retry_delivery(self, delivery_id: str) -> MessageDelivery:
        async with self.sessions.begin() as session:
            delivery = await session.get(
                MessageDelivery,
                delivery_id,
                with_for_update=True,
            )
            if delivery is None:
                raise KeyError(delivery_id)
            if delivery.status not in {
                DeliveryStatus.FAILED.value,
                DeliveryStatus.DEAD.value,
            }:
                raise ValueError("Only failed deliveries can be retried")
            delivery.status = DeliveryStatus.PENDING.value
            delivery.last_error = None
            delivery.next_attempt_at = utcnow()
            old_events = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.aggregate_id == delivery.message_id,
                            OutboxEvent.status.in_(
                                [
                                    DeliveryStatus.FAILED.value,
                                    DeliveryStatus.DEAD.value,
                                ]
                            ),
                        )
                    )
                ).all()
            )
            for event in old_events:
                if str(event.payload_json.get("delivery_id")) == delivery.id:
                    event.status = DeliveryStatus.DEAD.value
                    event.locked_at = None
                    event.last_error = "Superseded by manual retry"
            session.add(
                OutboxEvent(
                    event_type="message.delivery.requested",
                    aggregate_id=delivery.message_id,
                    payload_json={"delivery_id": delivery.id},
                )
            )
            return delivery

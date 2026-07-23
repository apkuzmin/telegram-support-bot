from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from support_bot.omnichannel.enums import (
    Channel,
    ConversationStatus,
    DeliveryStatus,
    MessageKind,
    SenderType,
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def uuid_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "support_customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    identities: Mapped[list["ChannelIdentity"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class ChannelIdentity(Base):
    __tablename__ = "support_channel_identities"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_support_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("support_customers.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    customer: Mapped[Customer] = relationship(back_populates="identities")


class Conversation(Base):
    __tablename__ = "support_conversations"
    __table_args__ = (
        Index("ix_support_conversations_status_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("support_customers.id", ondelete="CASCADE"), nullable=False
    )
    customer_channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=ConversationStatus.NEW.value, nullable=False
    )
    assigned_operator_id: Mapped[str | None] = mapped_column(String(255))
    telegram_topic_id: Mapped[int | None] = mapped_column(BigInteger)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "support_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "origin_channel",
            "origin_external_id",
            name="uq_support_message_origin",
        ),
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_support_message_sequence"
        ),
        Index(
            "ix_support_messages_conversation_sequence",
            "conversation_id",
            "sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("support_conversations.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_id: Mapped[str | None] = mapped_column(String(255))
    origin_channel: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), default=MessageKind.TEXT.value, nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text)
    reply_to_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("support_messages.id", ondelete="SET NULL")
    )
    attachments_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    edited_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class MessageDelivery(Base):
    __tablename__ = "support_message_deliveries"
    __table_args__ = (
        UniqueConstraint("message_id", "channel", "target", name="uq_support_delivery"),
        UniqueConstraint(
            "channel",
            "external_chat_id",
            "external_message_id",
            name="uq_support_external_delivery",
        ),
        Index("ix_support_delivery_due", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("support_messages.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=DeliveryStatus.PENDING.value, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    external_chat_id: Mapped[str | None] = mapped_column(String(255))
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Base):
    __tablename__ = "support_outbox"
    __table_args__ = (
        Index("ix_support_outbox_due", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=DeliveryStatus.PENDING.value, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ConversationRead(Base):
    __tablename__ = "support_conversation_reads"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("support_conversations.id", ondelete="CASCADE"), primary_key=True
    )
    actor_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class StoredFile(Base):
    __tablename__ = "support_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("support_customers.id", ondelete="CASCADE"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    attached_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_claimed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RealtimeEvent(Base):
    __tablename__ = "support_realtime_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topics_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


__all__ = [
    "Base",
    "Channel",
    "ChannelIdentity",
    "Conversation",
    "ConversationRead",
    "ConversationStatus",
    "Customer",
    "DeliveryStatus",
    "Message",
    "MessageDelivery",
    "MessageKind",
    "OutboxEvent",
    "RealtimeEvent",
    "SenderType",
    "StoredFile",
    "utcnow",
]

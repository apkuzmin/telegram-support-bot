from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    TELEGRAM_USER = "telegram_user"
    TELEGRAM_OPERATOR = "telegram_operator"
    WEB_USER = "web_user"
    WEB_OPERATOR = "web_operator"


class SenderType(StrEnum):
    CUSTOMER = "customer"
    OPERATOR = "operator"
    SYSTEM = "system"


class ConversationStatus(StrEnum):
    NEW = "new"
    OPEN = "open"
    CLOSED = "closed"


class MessageKind(StrEnum):
    TEXT = "text"
    FILE = "file"
    SYSTEM = "system"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    DEAD = "dead"

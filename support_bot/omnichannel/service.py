from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from support_bot.omnichannel.enums import (
    Channel,
    MessageKind,
    SenderType,
)
from support_bot.omnichannel.models import Conversation, Message
from support_bot.omnichannel.realtime import RealtimeHub
from support_bot.omnichannel.storage import (
    CustomerContext,
    DeliveryTarget,
    OmnichannelStore,
)


@dataclass(frozen=True)
class CreatedSession:
    context: CustomerContext
    external_user_id: str


class SupportService:
    def __init__(self, store: OmnichannelStore, realtime: RealtimeHub) -> None:
        self.store = store
        self.realtime = realtime

    async def create_web_session(
        self,
        *,
        external_user_id: str | None,
        display_name: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> CreatedSession:
        resolved_id = external_user_id or f"guest:{uuid.uuid4()}"
        context = await self.store.get_or_create_customer_context(
            channel=Channel.WEB_USER,
            external_id=resolved_id,
            display_name=display_name,
            metadata=metadata,
        )
        return CreatedSession(context=context, external_user_id=resolved_id)

    async def ensure_telegram_customer(
        self,
        *,
        telegram_user_id: int,
        display_name: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> CustomerContext:
        return await self.store.get_or_create_customer_context(
            channel=Channel.TELEGRAM_USER,
            external_id=str(telegram_user_id),
            display_name=display_name,
            metadata=metadata,
        )

    def _delivery_targets(
        self,
        *,
        conversation: Conversation,
        sender_type: SenderType,
        origin_channel: Channel,
    ) -> list[DeliveryTarget]:
        targets: list[DeliveryTarget] = []
        if sender_type is SenderType.CUSTOMER:
            if origin_channel is not Channel.TELEGRAM_OPERATOR:
                targets.append(
                    DeliveryTarget(Channel.TELEGRAM_OPERATOR, conversation.id)
                )
            return targets

        if origin_channel is not Channel.TELEGRAM_OPERATOR:
            targets.append(
                DeliveryTarget(Channel.TELEGRAM_OPERATOR, conversation.id)
            )
        customer_channel = Channel(conversation.customer_channel)
        if origin_channel is not customer_channel:
            targets.append(DeliveryTarget(customer_channel, conversation.id))
        return targets

    async def create_message(
        self,
        *,
        conversation: Conversation,
        sender_type: SenderType,
        sender_id: str | None,
        origin_channel: Channel,
        origin_external_id: str,
        text: str | None,
        reply_to_message_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Message, bool]:
        kind = MessageKind.FILE if attachments and not text else MessageKind.TEXT
        message, created = await self.store.create_message(
            conversation_id=conversation.id,
            sender_type=sender_type,
            sender_id=sender_id,
            origin_channel=origin_channel,
            origin_external_id=origin_external_id,
            text=text,
            kind=kind,
            reply_to_message_id=reply_to_message_id,
            attachments=attachments,
            metadata=metadata,
            deliveries=self._delivery_targets(
                conversation=conversation,
                sender_type=sender_type,
                origin_channel=origin_channel,
            ),
        )
        return message, created

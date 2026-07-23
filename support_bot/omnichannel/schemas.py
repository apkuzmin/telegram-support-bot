from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

from pydantic import AliasPath, BaseModel, ConfigDict, Field, field_validator

from support_bot.omnichannel.enums import ConversationStatus


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WidgetSessionRequest(StrictRequest):
    display_name: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    identity_token: str | None = None
    resume_token: str | None = None

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_bounded(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        if len(
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        ) > 8 * 1024:
            raise ValueError("Metadata exceeds 8192 bytes")
        return value


class WidgetSessionResponse(BaseModel):
    token: str
    customer_id: str
    conversation_id: str
    expires_in: int


class MessageCreate(StrictRequest):
    text: str | None = Field(default=None, max_length=20_000)
    reply_to_message_id: str | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    idempotency_key: str = Field(min_length=8, max_length=255)


class MessageEdit(StrictRequest):
    text: str = Field(max_length=20_000)


class DeliveryView(BaseModel):
    id: str
    channel: str
    target: str
    status: str
    attempts: int
    external_message_id: str | None
    last_error: str | None = None


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    sequence: int
    sender_type: str
    sender_id: str | None
    origin_channel: str
    kind: str
    text: str | None
    structured_content: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasPath(
            "metadata_json",
            "structured_content",
        ),
    )
    reply_to_message_id: str | None
    attachments_json: list[dict[str, Any]]
    created_at: dt.datetime
    edited_at: dt.datetime | None
    deliveries: list[DeliveryView] = Field(default_factory=list)


class MessagePage(BaseModel):
    items: list[MessageView]
    next_after_sequence: int


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    customer_channel: str
    status: str
    assigned_operator_id: str | None
    telegram_topic_id: int | None
    created_at: dt.datetime
    updated_at: dt.datetime
    customer_display_name: str | None = None
    last_sequence: int = 0


class ConversationPage(BaseModel):
    items: list[ConversationView]
    next_offset: int | None = None


class ConversationPatch(StrictRequest):
    status: ConversationStatus | None = None
    assigned_operator_id: str | None = Field(default=None, max_length=255)


class ReadUpdate(StrictRequest):
    last_sequence: int = Field(ge=0)


class ReadState(BaseModel):
    customer_last_sequence: int
    operator_last_sequence: int


class HealthResponse(BaseModel):
    status: Literal["ok"]

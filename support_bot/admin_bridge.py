from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from dataclasses import dataclass
from datetime import timezone
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from aiogram import Bot
from aiogram.types import BufferedInputFile, Message

from support_bot.db import Database
from support_bot.telegram_utils import extract_file_id

logger = logging.getLogger(__name__)
MAX_BRIDGE_PHOTO_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class AdminBridgeSettings:
    base_url: str
    token: str
    bot_instance_id: str
    operator_group_id: int
    poll_interval_seconds: float = 2.0


class AdminSupportBridge:
    """Optional HTTP adapter between Telegram and an external support backend."""

    def __init__(
        self,
        settings: AdminBridgeSettings,
        *,
        session: ClientSession | None = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._owns_session = session is None

    async def start(self) -> None:
        if self._session is None:
            self._session = ClientSession(timeout=ClientTimeout(total=10))

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None

    async def publish_user_message(self, message: Message, topic_id: int, db: Database) -> bool:
        if message.from_user is None:
            return False
        return await self._publish(
            db=db,
            event_id=f"user:{message.chat.id}:{message.message_id}",
            direction="user",
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            topic_id=topic_id,
            message=message,
        )

    async def publish_operator_message(self, message: Message, user_id: int, db: Database) -> bool:
        return await self._publish(
            db=db,
            event_id=f"operator:{message.chat.id}:{message.message_id}",
            direction="operator",
            user_id=user_id,
            username=None,
            first_name="",
            last_name="",
            topic_id=message.message_thread_id,
            message=message,
        )

    async def _publish(
        self,
        *,
        db: Database,
        event_id: str,
        direction: str,
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        topic_id: int | None,
        message: Message,
    ) -> bool:
        payload = {
            "bot_instance_id": self._settings.bot_instance_id,
            "event_id": event_id,
            "direction": direction,
            "topic_id": topic_id,
            "user": {
                "id": user_id,
                "username": username or "",
                "first_name": first_name or "",
                "last_name": last_name or "",
            },
            "message": {
                "chat_id": message.chat.id,
                "message_id": message.message_id,
                "topic_message_id": message.message_id if message.is_topic_message else None,
                "content_type": message.content_type,
                "text": message.text,
                "caption": message.caption,
                "file_id": extract_file_id(message),
                "created_at": message.date.astimezone(timezone.utc).isoformat(),
            },
        }
        attachment = await self._download_message_attachment(message)
        if attachment is not None:
            payload["message"]["attachment"] = attachment
        await db.upsert_admin_bridge_event(
            event_id=event_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        for attempt in range(3):
            try:
                await self._request("POST", "/api/v1/support/bridge/events", json=payload)
                await db.delete_admin_bridge_event(event_id)
                return True
            except (ClientError, asyncio.TimeoutError, RuntimeError) as exc:
                if attempt == 2:
                    # Telegram delivery remains the source of truth while the bridge is
                    # unavailable. The stable event_id makes a manual replay safe.
                    logger.warning(
                        "support admin bridge event failed",
                        extra={"event_id": event_id, "error": str(exc)},
                    )
                    return False
                await asyncio.sleep(0.5 * (2**attempt))
        return False

    async def poll_outbox(self, bot: Bot, db: Database, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._flush_pending_events(db)
                response = await self._request(
                    "GET",
                    "/api/v1/support/bridge/outbox",
                    params={
                        "bot_instance_id": self._settings.bot_instance_id,
                        "limit": "20",
                    },
                )
                for item in response.get("items", []):
                    await self._deliver_outbox_item(bot, db, item)
            except asyncio.CancelledError:
                raise
            except (ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
                logger.warning("support admin bridge outbox poll failed", extra={"error": str(exc)})

            try:
                await asyncio.wait_for(stop.wait(), timeout=self._settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _deliver_outbox_item(self, bot: Bot, db: Database, item: dict[str, Any]) -> None:
        message_id = int(item["id"])
        delivered = await self._find_delivered(db, message_id)
        if delivered is not None:
            await self._ack(
                message_id,
                status="sent",
                telegram_message_id=delivered[0],
                topic_message_id=delivered[1],
            )
            return
        user_id = int(item["telegram_user_id"])
        topic_id_raw = item.get("topic_id")
        text = str(item.get("text") or item.get("caption") or "")
        if topic_id_raw is None:
            await self._ack(message_id, status="failed", error="conversation has no topic_id")
            return
        topic_id = int(topic_id_raw)
        topic_message = None
        try:
            if bool(item.get("has_attachment")):
                attachment_data = await self._request_bytes(
                    "GET",
                    f"/api/v1/support/bridge/outbox/{message_id}/attachment",
                    params={"bot_instance_id": self._settings.bot_instance_id},
                )
                file_name = str(item.get("attachment_name") or f"photo-{message_id}.jpg")
                topic_message = await bot.send_photo(
                    chat_id=self._settings.operator_group_id,
                    message_thread_id=topic_id,
                    photo=BufferedInputFile(attachment_data, filename=file_name),
                    caption=text or None,
                )
                if not topic_message.photo:
                    raise RuntimeError("Telegram did not return the uploaded photo")
                private_message = await bot.send_photo(
                    chat_id=user_id,
                    photo=topic_message.photo[-1].file_id,
                    caption=text or None,
                )
                delivered_content_type = "photo"
                delivered_text = None
                delivered_caption = text or None
                delivered_file_id = (
                    private_message.photo[-1].file_id if private_message.photo else None
                )
            else:
                topic_message = await bot.send_message(
                    chat_id=self._settings.operator_group_id,
                    message_thread_id=topic_id,
                    text=text,
                )
                private_message = await bot.send_message(chat_id=user_id, text=text)
                delivered_content_type = "text"
                delivered_text = text
                delivered_caption = None
                delivered_file_id = None
            async with db.transaction():
                await db.log_message_link(
                    user_id=user_id,
                    source_chat_id=user_id,
                    source_message_id=private_message.message_id,
                    target_chat_id=self._settings.operator_group_id,
                    target_message_id=topic_message.message_id,
                    commit=False,
                )
                await db.log_message_link(
                    user_id=user_id,
                    source_chat_id=self._settings.operator_group_id,
                    source_message_id=topic_message.message_id,
                    target_chat_id=user_id,
                    target_message_id=private_message.message_id,
                    commit=False,
                )
                await db.log_message(
                    user_id=user_id,
                    direction="operator",
                    chat_id=self._settings.operator_group_id,
                    message_id=topic_message.message_id,
                    content_type=delivered_content_type,
                    text=delivered_text,
                    caption=delivered_caption,
                    file_id=delivered_file_id,
                    payload_json=None,
                    commit=False,
                )
                await db.record_admin_bridge_delivery(
                    outbox_id=message_id,
                    telegram_message_id=private_message.message_id,
                    topic_message_id=topic_message.message_id,
                    commit=False,
                )
            await self._ack(
                message_id,
                status="sent",
                telegram_message_id=private_message.message_id,
                topic_message_id=topic_message.message_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("support admin bridge delivery failed", extra={"message_id": message_id})
            if topic_message is not None:
                try:
                    await bot.delete_message(
                        chat_id=self._settings.operator_group_id,
                        message_id=topic_message.message_id,
                    )
                except Exception:
                    logger.warning(
                        "failed to roll back topic copy",
                        extra={"message_id": message_id, "topic_message_id": topic_message.message_id},
                    )
            await self._ack(message_id, status="failed", error=str(exc)[:1000])

    async def _download_message_attachment(self, message: Message) -> dict[str, Any] | None:
        if message.content_type != "photo" or not message.photo:
            return None
        destination = io.BytesIO()
        await message.bot.download(message.photo[-1], destination=destination)
        data = destination.getvalue()
        if not data:
            raise RuntimeError("Telegram returned an empty photo")
        if len(data) > MAX_BRIDGE_PHOTO_BYTES:
            raise RuntimeError("Telegram photo exceeds the 10 MB admin bridge limit")
        return {
            "file_name": f"photo-{message.message_id}.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": len(data),
            "data_base64": base64.b64encode(data).decode("ascii"),
        }

    async def _flush_pending_events(self, db: Database) -> None:
        rows = await db.list_admin_bridge_events(limit=50)
        for event_id, payload_json in rows:
            payload = json.loads(str(payload_json))
            await self._request("POST", "/api/v1/support/bridge/events", json=payload)
            await db.delete_admin_bridge_event(event_id)

    async def _find_delivered(self, db: Database, outbox_id: int) -> tuple[int, int] | None:
        return await db.find_admin_bridge_delivery(outbox_id)

    async def _ack(
        self,
        message_id: int,
        *,
        status: str,
        error: str = "",
        telegram_message_id: int | None = None,
        topic_message_id: int | None = None,
    ) -> None:
        await self._request(
            "POST",
            f"/api/v1/support/bridge/outbox/{message_id}/ack",
            json={
                "bot_instance_id": self._settings.bot_instance_id,
                "status": status,
                "error": error,
                "telegram_message_id": telegram_message_id,
                "topic_message_id": topic_message_id,
            },
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("AdminSupportBridge.start() must be called first")
        url = f"{self._settings.base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {self._settings.token}"}
        async with self._session.request(method, url, headers=headers, **kwargs) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"bridge returned HTTP {response.status}: {payload}")
            if not isinstance(payload, dict):
                raise RuntimeError("bridge returned a non-object response")
            return payload

    async def _request_bytes(self, method: str, path: str, **kwargs: Any) -> bytes:
        if self._session is None:
            raise RuntimeError("AdminSupportBridge.start() must be called first")
        url = f"{self._settings.base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {self._settings.token}"}
        async with self._session.request(method, url, headers=headers, **kwargs) as response:
            if response.status >= 400:
                payload = await response.text()
                raise RuntimeError(f"bridge returned HTTP {response.status}: {payload[:1000]}")
            return await response.read()

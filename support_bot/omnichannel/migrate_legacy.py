from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import aiosqlite

from support_bot.omnichannel.enums import (
    Channel,
    ConversationStatus,
    MessageKind,
    SenderType,
)
from support_bot.omnichannel.storage import OmnichannelStore


async def _table_exists(
    connection: aiosqlite.Connection, table: str
) -> bool:
    row = await (
        await connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
    ).fetchone()
    return row is not None


async def migrate_legacy(
    *,
    legacy_path: str,
    database_url: str,
) -> dict[str, int]:
    store = OmnichannelStore(database_url)
    counts = {"users": 0, "conversations": 0, "messages": 0, "deliveries": 0}
    legacy = await aiosqlite.connect(legacy_path)
    legacy.row_factory = aiosqlite.Row
    try:
        if not await _table_exists(legacy, "users"):
            raise RuntimeError("Legacy database does not contain users")
        users = await (await legacy.execute("SELECT * FROM users")).fetchall()
        conversations_by_user: dict[int, Any] = {}
        if await _table_exists(legacy, "conversations"):
            rows = await (
                await legacy.execute("SELECT * FROM conversations")
            ).fetchall()
            conversations_by_user = {int(row["user_id"]): row for row in rows}
        links: dict[tuple[int, int], Any] = {}
        if await _table_exists(legacy, "message_links"):
            rows = await (
                await legacy.execute("SELECT * FROM message_links")
            ).fetchall()
            links = {
                (int(row["source_chat_id"]), int(row["source_message_id"])): row
                for row in rows
            }

        for user in users:
            user_id = int(user["user_id"])
            display_name = " ".join(
                part
                for part in (user["first_name"], user["last_name"])
                if part
            ) or (f"@{user['username']}" if user["username"] else str(user_id))
            legacy_conversation = conversations_by_user.get(user_id)
            if legacy_conversation is not None:
                context = await store.get_or_create_import_context(
                    channel=Channel.TELEGRAM_USER,
                    external_id=str(user_id),
                    display_name=display_name,
                    telegram_topic_id=int(legacy_conversation["topic_id"]),
                    metadata={"username": user["username"]},
                )
                if not bool(legacy_conversation["active"]):
                    await store.update_conversation(
                        context.conversation.id,
                        status=ConversationStatus.CLOSED,
                    )
                counts["conversations"] += 1
            else:
                context = await store.get_or_create_customer_context(
                    channel=Channel.TELEGRAM_USER,
                    external_id=str(user_id),
                    display_name=display_name,
                    metadata={"username": user["username"]},
                )
            counts["users"] += 1

            if not await _table_exists(legacy, "messages"):
                continue
            messages = await (
                await legacy.execute(
                    "SELECT * FROM messages WHERE user_id=? ORDER BY created_at, id",
                    (user_id,),
                )
            ).fetchall()
            for old in messages:
                direction = str(old["direction"])
                channel = (
                    Channel.TELEGRAM_USER
                    if direction == "user"
                    else Channel.TELEGRAM_OPERATOR
                )
                sender_type = (
                    SenderType.CUSTOMER
                    if direction == "user"
                    else SenderType.OPERATOR
                )
                chat_id = int(old["chat_id"])
                message_id = int(old["message_id"])
                payload: dict[str, Any] = {}
                if old["payload_json"]:
                    try:
                        payload = json.loads(old["payload_json"])
                    except json.JSONDecodeError:
                        payload = {}
                attachments = []
                if old["file_id"]:
                    attachments.append(
                        {
                            "unavailable": True,
                            "reason": "legacy_telegram_file",
                            "telegram_file_id": old["file_id"],
                        }
                    )
                structured_content = (
                    {
                        "type": old["content_type"],
                        "data": payload.get(old["content_type"]),
                    }
                    if old["content_type"]
                    in {
                        "contact",
                        "dice",
                        "game",
                        "location",
                        "poll",
                        "venue",
                    }
                    and payload.get(old["content_type"]) is not None
                    else None
                )
                stored, created = await store.create_message(
                    conversation_id=context.conversation.id,
                    sender_type=sender_type,
                    sender_id=(
                        context.customer.id
                        if sender_type is SenderType.CUSTOMER
                        else None
                    ),
                    origin_channel=channel,
                    origin_external_id=f"{chat_id}:{message_id}",
                    text=old["text"] or old["caption"],
                    kind=(
                        MessageKind.FILE
                        if attachments
                        else (
                            MessageKind.STRUCTURED
                            if structured_content is not None
                            else MessageKind.TEXT
                        )
                    ),
                    attachments=attachments,
                    metadata={
                        "telegram_chat_id": str(chat_id),
                        "telegram_message_id": str(message_id),
                        "telegram_content_type": old["content_type"],
                        "telegram_payload": payload,
                        "structured_content": structured_content,
                        "legacy_import": True,
                    },
                    emit_realtime=False,
                )
                if created:
                    counts["messages"] += 1
                link = links.get((chat_id, message_id))
                if link is None:
                    continue
                target_chat_id = int(link["target_chat_id"])
                target_message_id = int(link["target_message_id"])
                delivery_channel = (
                    Channel.TELEGRAM_OPERATOR
                    if channel is Channel.TELEGRAM_USER
                    else Channel.TELEGRAM_USER
                )
                await store.import_sent_delivery(
                    message_id=stored.id,
                    channel=delivery_channel,
                    target=context.conversation.id,
                    external_chat_id=str(target_chat_id),
                    external_message_id=str(target_message_id),
                )
                counts["deliveries"] += 1
        return counts
    finally:
        await legacy.close()
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import the legacy Telegram SQLite database"
    )
    parser.add_argument("--legacy-db", required=True)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    result = asyncio.run(
        migrate_legacy(
            legacy_path=args.legacy_db,
            database_url=args.database_url,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

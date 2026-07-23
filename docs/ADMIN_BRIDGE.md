# External admin bridge

The optional admin bridge mirrors Telegram support conversations to an
external HTTP service and polls that service for replies written in a support
dashboard. It is disabled unless all three `ADMIN_BRIDGE_*` variables are set.
The regular Telegram-to-topic workflow remains the source of truth and keeps
working when the external service is unavailable.

## Configuration

```dotenv
ADMIN_BRIDGE_URL=https://support-api.example.com
ADMIN_BRIDGE_TOKEN=<shared bearer token, at least 32 characters>
ADMIN_BRIDGE_BOT_INSTANCE_ID=<stable UUID for this bot instance>
```

Every request uses:

```http
Authorization: Bearer <ADMIN_BRIDGE_TOKEN>
```

The external service should scope every operation by
`bot_instance_id`. Event IDs are stable and must be treated idempotently.

## Inbound Telegram events

The bot sends `POST /api/v1/support/bridge/events` after a customer message is
copied to its operator topic and after an operator message is copied to the
customer.

```json
{
  "bot_instance_id": "00000000-0000-0000-0000-000000000001",
  "event_id": "user:123456:42",
  "direction": "user",
  "topic_id": 77,
  "user": {
    "id": 123456,
    "username": "customer",
    "first_name": "Example",
    "last_name": "User"
  },
  "message": {
    "chat_id": 123456,
    "message_id": 42,
    "content_type": "text",
    "text": "I need help",
    "created_at": "2026-01-01T12:00:00+00:00"
  }
}
```

`direction` is `user` for private customer messages and `operator` for
operator-topic messages. The receiver should return any JSON object with a
successful HTTP status. Failed events are retained in SQLite and replayed with
the same `event_id`.

### Photo events

Telegram photos include a JPEG attachment directly in the event. The bridge
limit is 10 MB.

```json
{
  "message": {
    "content_type": "photo",
    "caption": "Screenshot",
    "file_id": "telegram-file-id",
    "attachment": {
      "file_name": "photo-42.jpg",
      "mime_type": "image/jpeg",
      "size_bytes": 12345,
      "data_base64": "<base64 data>"
    }
  }
}
```

The example only shows the changed `message` object; the surrounding event
fields are the same as for text.

## Dashboard outbox

The bot polls:

```http
GET /api/v1/support/bridge/outbox
    ?bot_instance_id=<UUID>
    &limit=20
```

The response is an object containing an `items` array. A text item requires:

```json
{
  "id": 501,
  "telegram_user_id": 123456,
  "topic_id": 77,
  "text": "Reply from support",
  "has_attachment": false
}
```

A photo item additionally uses:

```json
{
  "id": 502,
  "telegram_user_id": 123456,
  "topic_id": 77,
  "caption": "Requested screenshot",
  "has_attachment": true,
  "attachment_name": "answer.png",
  "attachment_mime": "image/png",
  "attachment_size": 45678
}
```

For an attachment item, the bot downloads the bytes from:

```http
GET /api/v1/support/bridge/outbox/{id}/attachment
    ?bot_instance_id=<UUID>
```

The response body must contain the image bytes. JPEG, PNG and WebP images up to
10 MB are supported. The bot uploads the photo to the existing operator topic
and reuses Telegram's returned `file_id` for the customer's private chat.

## Delivery acknowledgement

After delivery, the bot sends:

```http
POST /api/v1/support/bridge/outbox/{id}/ack
```

```json
{
  "bot_instance_id": "00000000-0000-0000-0000-000000000001",
  "status": "sent",
  "error": "",
  "telegram_message_id": 101,
  "topic_message_id": 100
}
```

On failure, `status` is `failed` and `error` contains a bounded diagnostic
message. Successful Telegram IDs are recorded in SQLite before acknowledgement,
so retrying an acknowledgement does not resend the same outbox item.

# Headless omnichannel support module

This repository exposes support conversations as an API. It does not render a
customer widget or operator dashboard. The host website is responsible only
for UI and for issuing signed identity/operator tokens.

## Runtime topology

Two processes share one PostgreSQL database and upload volume:

- `support_bot.omnichannel.api_main` serves REST, OpenAPI and WebSocket.
- `support_bot.omnichannel.telegram_main` receives Telegram updates and
  dispatches the transactional outbox.

The canonical message is written once to `support_messages`. Copies in a
Telegram topic, Telegram private chat or website connection are tracked in
`support_message_deliveries`. `support_outbox` makes canonical enqueue
idempotent and Telegram delivery retryable. `support_realtime_events` lets API
and Telegram run
in separate processes while WebSocket clients reconnect using an event cursor.

## Start locally

```bash
cp .env.example .env
# Set SUPPORT_AUTH_SECRET in .env, for example with: openssl rand -hex 32
docker compose up -d postgres api
```

The API is available at:

- OpenAPI UI: `http://127.0.0.1:8080/docs`
- OpenAPI JSON: `http://127.0.0.1:8080/openapi.json`
- Health: `http://127.0.0.1:8080/health`

Start the Telegram worker only after setting `BOT_TOKEN` and
`OPERATOR_GROUP_ID`:

```bash
docker compose up -d telegram-worker
```

In production set a random `SUPPORT_AUTH_SECRET` of at least 32 bytes and list
the exact website origins in `SUPPORT_ALLOWED_ORIGINS`. Do not expose
PostgreSQL or the upload directory publicly.

Set `SUPPORT_TRUSTED_HOSTS` to the public API host and internal health-check
hosts. `SUPPORT_EXPOSE_DOCS=0` is the production default; enable it only on an
internal or otherwise protected integration environment.

At the reverse proxy, rate-limit anonymous session creation, message sends,
file uploads and WebSocket handshakes per IP/account. Keep the API behind TLS,
apply an overall request-body limit, and expose only the API/health routes
needed by the site.

## Authentication contract

All API access after session creation uses:

```http
Authorization: Bearer <signed-token>
```

Tokens are standard JWTs signed with HS256 by `TokenSigner` in
`support_bot.omnichannel.auth`. The host site's backend and the support module
share `SUPPORT_AUTH_SECRET`. The browser must never receive this secret.

For a backend written in another language, use any JWT library with algorithm
`HS256`, audience `support-module`, and claims `sub`, `role`, `iat`, `exp`.
The `identity` and operator tokens do not contain `conversation_id`; customer
tokens returned by this module do. Reject algorithm substitution and validate
the audience and expiration on every request.

There are four token roles:

- `identity`: short-lived assertion created by the host backend for an
  authenticated site account.
- `customer`: scoped by the module to one customer and conversation.
- `operator`: access to operator endpoints.
- `admin`: same API access as operator; reserved for future administration.

Generate a development operator token:

```bash
python -m support_bot.omnichannel.cli issue-token \
  --role operator \
  --subject operator-42 \
  --ttl 3600
```

For an authenticated customer, the host backend issues an `identity` token
whose `sub` is the site's stable account ID. The frontend exchanges it:

```http
POST /api/v1/widget/sessions
Content-Type: application/json

{
  "identity_token": "<short-lived identity token>",
  "display_name": "Иван"
}
```

For an anonymous visitor, omit `identity_token`. The module creates a random
guest identity. Store the returned customer token in a secure cookie or the
host site's protected session. To refresh access without creating a second
dialog, call the same endpoint with `resume_token`. If that dialog has already
been closed, resuming creates a new dialog for the same customer; posting with
the stale closed-dialog token returns `409`.

An arbitrary account ID supplied by the browser is never trusted. Only a
correctly signed `identity` token can select a stable site identity.

## Customer API flow

1. Create or resume a session:

   `POST /api/v1/widget/sessions`

2. Load conversation history:

   `GET /api/v1/conversations/{conversation_id}/messages?after_sequence=0`

3. Send a message:

   `POST /api/v1/conversations/{conversation_id}/messages`

   ```json
   {
     "text": "Не могу войти",
     "reply_to_message_id": null,
     "attachment_ids": [],
     "idempotency_key": "019f-client-generated-unique-key"
   }
   ```

4. Mark the visible range as read:

   `POST /api/v1/conversations/{conversation_id}/read`

   ```json
   {"last_sequence": 12}
   ```

5. Edit a message created in the website channel:

   `PATCH /api/v1/conversations/{conversation_id}/messages/{message_id}`

   ```json
   {"text": "Исправленный текст"}
   ```

The frontend must generate one stable `idempotency_key` for each user send
action and reuse it after a timeout. Repeating the same request returns the
original message instead of creating a duplicate.

## Operator API flow

- List/search/filter:

  `GET /api/v1/operator/conversations?status=open&search=Иван&limit=50&offset=0`

  Continue with the returned `next_offset` until it is `null`.

- Load incremental history:

  `GET /api/v1/operator/conversations/{id}/messages?after_sequence=20`

- Reply:

  `POST /api/v1/operator/conversations/{id}/messages`

- Edit an answer created by the same website operator:

  `PATCH /api/v1/operator/conversations/{id}/messages/{message_id}`

- Assign or change status:

  `PATCH /api/v1/operator/conversations/{id}`

  ```json
  {
    "status": "open",
    "assigned_operator_id": "operator-42"
  }
  ```

- Mark read:

  `POST /api/v1/operator/conversations/{id}/read`

Message responses include `deliveries`. Operator responses include retry
attempts and the last delivery error; customer responses omit internal error
details. A failed/dead delivery can be manually queued again with
`POST /api/v1/operator/deliveries/{delivery_id}/retry`.

## Realtime contract

Connect without placing a token in the URL:

```text
wss://support.example.com/api/v1/ws
```

Immediately send the authentication frame:

```json
{"type": "auth", "token": "<customer-or-operator-token>"}
```

The server then returns:

```json
{"type": "ready", "event_id": 125}
```

Events currently include:

```json
{
  "type": "message.created",
  "event_id": 126,
  "conversation_id": "...",
  "message_id": "...",
  "sequence": 14
}
```

```json
{
  "type": "conversation.updated",
  "event_id": 127,
  "conversation_id": "..."
}
```

After receiving an event, fetch messages after the last rendered
`sequence`. Events are signals, not the source of message content.

On reconnect pass `after_event_id=<last processed event_id>`. The server reads
missed durable events from PostgreSQL. If the client no longer has a cursor,
reconnect without it and refresh the relevant histories through REST.

Send text `ping` to receive `pong`.

## Files

Customer upload:

`POST /api/v1/files` as `multipart/form-data`, field `upload`.

Operator upload:

`POST /api/v1/operator/conversations/{id}/files`.

Use the returned file IDs in `attachment_ids`. Download through
`GET /api/v1/files/{file_id}` with authorization. Customer access is limited
to files owned by that customer. `SUPPORT_MAX_UPLOAD_BYTES` applies while
streaming; partial files are removed after an error.

`LocalFileStore` is the default adapter. Replace it with an S3-compatible
implementation in production when API and Telegram workers do not share a
filesystem. Active browser content such as HTML, JavaScript and SVG is rejected
and downloads are served by opaque ID as attachments. Configure the reverse
proxy with the same or a lower request-body limit and add malware scanning if
the module accepts files from untrusted public traffic.

## Telegram behavior

- A Telegram private message creates/fetches the same canonical conversation.
- The worker creates one forum topic per conversation.
- Messages from Telegram users are copied to the topic.
- Website customer messages are sent into the same topic.
- Human replies in the topic are stored and delivered to the originating
  customer channel.
- Website operator replies are mirrored into the topic and delivered to the
  customer channel.
- Bot-authored topic messages are ignored on ingress, preventing loops.
- Closed or missing delivery attempts remain in outbox retry state rather than
  disappearing.

Only one Telegram polling worker may run for a bot token.
Delivery is at-least-once: if the worker is terminated after Telegram accepts
a send but before PostgreSQL records it, a retry can create a rare duplicate.
Canonical website history remains idempotent and ordered.

## Legacy SQLite migration

Back up the existing SQLite file first, apply the new PostgreSQL migration,
then run:

```bash
alembic upgrade head
python -m support_bot.omnichannel.migrate_legacy \
  --legacy-db ./support_bot.sqlite3 \
  --database-url postgresql+asyncpg://support:password@localhost/support
```

The importer is idempotent. It keeps Telegram user identities, active topic
IDs, logged messages and reply-copy mappings. Imported history does not enter
the outbox, so old messages are not sent again.

## Verification

Unit/integration suite:

```bash
python -m unittest discover -v
```

Local PostgreSQL/API/WebSocket smoke:

```bash
docker compose up -d --build postgres api
python -m scripts.smoke_headless \
  --base-url http://127.0.0.1:8080 \
  --secret "$SUPPORT_AUTH_SECRET"
```

The smoke test creates a customer session, receives a WebSocket event, sends a
customer message, sends an operator reply and verifies ordered history.

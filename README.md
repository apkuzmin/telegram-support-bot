# Telegram Support Bot

> **Each user → a separate forum topic for operators.**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![aiogram 3](https://img.shields.io/badge/aiogram-3-2CA5E0?logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A self-hosted Telegram support bot built with **aiogram 3**. Customers write to
the bot in private messages, while operators handle every conversation inside
a dedicated topic in a forum-enabled Telegram supergroup.

Text, media, replies and supported edits are synchronized in both directions.
The Telegram-only workflow works out of the box; an optional HTTP bridge and a
headless API can connect the same support flow to an external dashboard or
website.

<img src="https://github.com/user-attachments/assets/69be22c3-45a6-4586-a317-982113a630aa" width="600" alt="Telegram Support Bot with a separate forum topic for each customer">

## How it works

```text
Customer private chat  ↔  Telegram bot  ↔  Customer forum topic  ↔  Operators
                                  ↕
                     Optional external support backend
```

1. A customer sends a private message to the bot.
2. The bot creates or finds that customer's forum topic.
3. The message is copied into the topic with its formatting and media.
4. An operator replies inside the topic.
5. The reply is delivered back to the customer's private chat.

Routing and reply links are persisted, so each customer keeps the same topic
across restarts.

## Features

### Telegram support workflow

- **One customer → one forum topic** for operators
- Bidirectional text and media delivery
- Telegram entities, formatting and media captions
- Reply and manually selected quote mirroring
- Supported message edits synchronized in both directions
- Configurable `/start` greeting
- Automatic topic recreation when a linked topic was removed

### Reliability

- SQLite routing and reply mapping
- Optional message history
- Transaction-safe delivery bookkeeping
- Retryable external delivery with stable idempotency keys
- Duplicate protection for dashboard replies
- Telegram-only operation continues when an external backend is unavailable

### Optional integrations

- **External admin bridge** — mirror Telegram conversations to any compatible
  HTTP backend and send dashboard replies, text and photos back to Telegram
- **Headless omnichannel API** — REST, OpenAPI and WebSocket contracts backed by
  PostgreSQL for a website widget or operator dashboard

## Requirements

- Python **3.10+**; Python **3.11+** is recommended
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A supergroup with **Forum / Topics** enabled
- Bot permissions to manage topics and send messages in that supergroup

## Quick start

Clone the repository and install the dependencies:

```bash
git clone https://github.com/apkuzmin/telegram-support-bot.git
cd telegram-support-bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create the local configuration:

```bash
cp .env.example .env
```

Set the required values in `.env`:

```dotenv
BOT_TOKEN=123456:your_bot_token
OPERATOR_GROUP_ID=-1001234567890
DB_PATH=./support_bot.sqlite3
```

Run the Telegram bot:

```bash
python -m support_bot
```

The first private message from a customer creates a new topic in
`OPERATOR_GROUP_ID`. Further messages and operator replies use the same topic.

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Telegram bot token |
| `OPERATOR_GROUP_ID` | Yes | Forum-enabled supergroup ID, usually starting with `-100` |
| `DB_PATH` | Yes | SQLite database used for routing and reply links |
| `LOG_LEVEL` | No | Logging level; defaults to `INFO` |
| `LOG_MESSAGES` | No | Set to `0` to disable message history |
| `START_MESSAGE` | No | Greeting returned for `/start` |

See [.env.example](.env.example) for every available setting.

## Supported Telegram messages

- Formatted text and media captions
- Photos, videos, animations and documents
- Audio, voice messages and video notes
- Stickers
- Contacts, locations and venues
- Polls, dice and games
- Replies and manually selected quoted fragments

Messages are copied with Telegram's `copyMessage` API, preserving the original
content and formatting. Media-group items are delivered individually because
Telegram sends them as separate updates.

Edits are synchronized for formatted text, captions and Telegram-editable
photos, videos, animations, audio and documents. Voice-message captions can be
updated without replacing the voice file.

Telegram does not provide edit operations for stickers, video notes, contacts,
locations, venues, polls, dice or games. Service messages, paid media,
giveaways, giveaway winners and invoices cannot be copied by bots.

## External support dashboard bridge

The optional bridge mirrors Telegram messages to an external HTTP service and
polls that service for operator replies:

```dotenv
ADMIN_BRIDGE_URL=https://support-api.example.com
ADMIN_BRIDGE_TOKEN=<shared bearer token, at least 32 characters>
ADMIN_BRIDGE_BOT_INSTANCE_ID=<stable UUID for this bot instance>
```

It supports:

- Incoming and outgoing text messages
- Incoming and outgoing photos with captions
- Durable retries when the backend is temporarily unavailable
- Delivery acknowledgements
- Idempotent events and duplicate-safe outbox processing

If these variables are absent, the regular Telegram-only workflow is unchanged.
See [External admin bridge](docs/ADMIN_BRIDGE.md) for the complete HTTP
contract.

## Headless omnichannel module

The repository also includes an independent support API for integrating a
website widget or operator dashboard. It provides:

- REST and OpenAPI endpoints
- WebSocket conversation updates with resumable cursors
- PostgreSQL storage and a transactional delivery outbox
- Customer, operator and admin token roles
- File uploads and Telegram delivery tracking

The module does not include a frontend. The integrating application owns the
customer and operator interfaces.

For local development:

```bash
cp .env.example .env
# Set SUPPORT_AUTH_SECRET in .env, for example:
openssl rand -hex 32
docker compose up -d postgres api telegram-worker
```

See [Headless integration](docs/HEADLESS_INTEGRATION.md) for architecture,
authentication, deployment and API examples.

## Troubleshooting

### Topics are not created

- Confirm the operator chat is a supergroup with Forum / Topics enabled.
- Give the bot permission to manage topics and send messages.
- Verify that `OPERATOR_GROUP_ID` contains the full negative supergroup ID.

### Messages reach the bot but not operators

- Check that the bot is still a member of the operator group.
- Inspect the application log for Telegram permission errors.
- Verify that the configured SQLite path is writable.

### External dashboard delivery is unavailable

- Confirm all three `ADMIN_BRIDGE_*` variables are set.
- Verify that the bridge URL is reachable from the bot host.
- Use the same bearer token and bot instance UUID on both sides.

## Project documentation

- [External admin bridge](docs/ADMIN_BRIDGE.md)
- [Headless integration](docs/HEADLESS_INTEGRATION.md)

## Contributing

Issues and pull requests are welcome. Keep changes focused, add tests for new
delivery behavior and avoid committing local tokens, chat IDs or runtime
configuration.

## License

[MIT](LICENSE)

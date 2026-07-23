# Telegram Support Bot (aiogram 3 + Topics)


Open-source Telegram support bot: users chat with the bot in private messages, while operators work inside a **forum-enabled supergroup (Topics)**.  
For each user, the bot automatically creates a **separate forum topic** and mirrors the entire conversation there. Operator replies from the topic are sent back to the user’s private chat.

<img src="https://github.com/user-attachments/assets/69be22c3-45a6-4586-a317-982113a630aa" width="600" alt="Telegram Support Bot">

## Features

- **One user → one forum topic** for operators
- Bidirectional message forwarding (**user ↔ operators**)
- Telegram text formatting is preserved in both directions
- Reply mirroring across chats (reply in topic ↔ reply in private chat)
- Automatic topic creation
- **SQLite** for routing + reply mapping, optional message history
- Built with **aiogram 3**

## Requirements

- Python **3.10+** (recommended **3.11+**)
- An operator **supergroup with Forum / Topics enabled**
- Bot permissions in the supergroup:
  - **Manage Topics**
  - Permission to send messages

## Quick Start

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
2.	Create a .env file based on .env.example and set:
- BOT_TOKEN
- OPERATOR_GROUP_ID — supergroup ID (e.g. -100...)
- DB_PATH — SQLite database path (required for routing)
- LOG_MESSAGES — set to 0 to disable message history logging
- START_MESSAGE — optional `/start` greeting (defaults to English)

3.	Run the bot:
```bash
python -m support_bot
```

### How It Works
- A user sends a message to the bot in private chat.
- The `/start` command answers with `START_MESSAGE` from the environment.
- The bot creates (or finds) a forum topic in OPERATOR_GROUP_ID linked to that user.
- All user messages are mirrored into that topic.
- Replies are mirrored when the original message exists on the other side.
- SQLite stores routing and reply links. Message history is stored in SQLite (DB_PATH). Set LOG_MESSAGES=0 to disable history logging.

### Supported Telegram Messages

- Formatted text and media captions, including Telegram message entities
- Replies and manually selected quoted fragments, including quote formatting
- Photos, videos, animations, documents, audio, voice messages, video notes, and stickers
- Contacts, locations, venues, polls, dice, and games

Messages are copied through Telegram's `copyMessage` API in both directions, so
their original content and formatting are retained. Media-group items are
delivered, but album grouping is not currently retained because updates are
processed one message at a time.

Edits are synchronized in both directions for formatted text, media captions,
and Telegram-editable photo, video, animation, audio, and document messages.
Voice-message captions are synchronized without replacing the voice file.
Telegram does not provide edit operations for stickers, video notes, contacts,
locations, venues, polls, dice, or games, so those copied messages cannot be
updated in place.

Telegram does not allow bots to copy service messages, paid media, giveaways,
giveaway winners, or invoices. Quiz polls can be copied only when Telegram has
provided the correct answer to the bot.

### Notes
The bot works only with supergroups that have Topics (Forum) enabled.
If topics are not being created, check that the bot has the Manage Topics permission.

### License

MIT

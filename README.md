# Telegram Support Bot (aiogram 3 + Topics)


Open-source Telegram support bot: users chat with the bot in private messages, while operators work inside a **forum-enabled supergroup (Topics)**.  
For each user, the bot automatically creates a **separate forum topic** and mirrors the entire conversation there. Operator replies from the topic are sent back to the user’s private chat.

<img src="https://github.com/user-attachments/assets/69be22c3-45a6-4586-a317-982113a630aa" width="600" alt="Telegram Support Bot">
## Features

- **One user → one forum topic** for operators
- Bidirectional message forwarding (**user ↔ operators**)
- Automatic topic creation
- Optional message history stored in **SQLite**
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
- DB_PATH — SQLite database path (optional)
- LOG_MESSAGES — set to 0 to disable SQLite logging

3.	Run the bot:
```bash
python -m support_bot
```

### How It Works
- A user sends a message to the bot in private chat.
- The bot creates (or finds) a forum topic in OPERATOR_GROUP_ID linked to that user.
- All user messages are mirrored into that topic.
- Message history is stored in SQLite (DB_PATH). Set LOG_MESSAGES=0 to disable logging.

### Notes
The bot works only with supergroups that have Topics (Forum) enabled.
If topics are not being created, check that the bot has the Manage Topics permission.

### License

MIT

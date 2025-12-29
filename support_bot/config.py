from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    bot_token: str
    operator_group_id: int
    db_path: str
    log_level: str = "INFO"
    log_messages: bool = True


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Missing BOT_TOKEN env var")

    operator_group_id_raw = os.getenv("OPERATOR_GROUP_ID")
    if not operator_group_id_raw:
        raise RuntimeError("Missing OPERATOR_GROUP_ID env var")

    try:
        operator_group_id = int(operator_group_id_raw)
    except ValueError as exc:
        raise RuntimeError("OPERATOR_GROUP_ID must be an integer") from exc

    db_path = os.getenv("DB_PATH", "./support_bot.sqlite3")
    log_level = os.getenv("LOG_LEVEL", "INFO")
    log_messages = os.getenv("LOG_MESSAGES", "1") != "0"

    return Config(
        bot_token=bot_token,
        operator_group_id=operator_group_id,
        db_path=db_path,
        log_level=log_level,
        log_messages=log_messages,
    )

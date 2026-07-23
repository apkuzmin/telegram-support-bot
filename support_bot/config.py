from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID


DEFAULT_START_MESSAGE = "Hello! How can I help you?"


@dataclass(frozen=True)
class Config:
    bot_token: str
    operator_group_id: int
    db_path: str
    log_level: str = "INFO"
    log_messages: bool = True
    start_message: str = DEFAULT_START_MESSAGE
    admin_bridge_url: str = ""
    admin_bridge_token: str = ""
    admin_bridge_bot_instance_id: str = ""

    @property
    def admin_bridge_enabled(self) -> bool:
        return bool(self.admin_bridge_url)


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
    start_message = os.getenv("START_MESSAGE", DEFAULT_START_MESSAGE)
    admin_bridge_url = os.getenv("ADMIN_BRIDGE_URL", "").strip().rstrip("/")
    admin_bridge_token = os.getenv("ADMIN_BRIDGE_TOKEN", "").strip()
    admin_bridge_bot_instance_id = os.getenv(
        "ADMIN_BRIDGE_BOT_INSTANCE_ID", ""
    ).strip()

    bridge_values = (
        admin_bridge_url,
        admin_bridge_token,
        admin_bridge_bot_instance_id,
    )
    if any(bridge_values) and not all(bridge_values):
        raise RuntimeError(
            "ADMIN_BRIDGE_URL, ADMIN_BRIDGE_TOKEN and "
            "ADMIN_BRIDGE_BOT_INSTANCE_ID must be set together"
        )
    if admin_bridge_url and not admin_bridge_url.startswith(("http://", "https://")):
        raise RuntimeError("ADMIN_BRIDGE_URL must use http:// or https://")
    if admin_bridge_token and len(admin_bridge_token) < 32:
        raise RuntimeError("ADMIN_BRIDGE_TOKEN must contain at least 32 characters")
    if admin_bridge_bot_instance_id:
        try:
            UUID(admin_bridge_bot_instance_id)
        except ValueError as exc:
            raise RuntimeError(
                "ADMIN_BRIDGE_BOT_INSTANCE_ID must be a UUID"
            ) from exc

    return Config(
        bot_token=bot_token,
        operator_group_id=operator_group_id,
        db_path=db_path,
        log_level=log_level,
        log_messages=log_messages,
        start_message=start_message,
        admin_bridge_url=admin_bridge_url,
        admin_bridge_token=admin_bridge_token,
        admin_bridge_bot_instance_id=admin_bridge_bot_instance_id,
    )

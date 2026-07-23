from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram import F
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from support_bot.admin_bridge import AdminBridgeSettings, AdminSupportBridge
from support_bot.config import load_config
from support_bot.db import Database
from support_bot.handlers.operator import router as operator_router
from support_bot.handlers.user import router as user_router
from support_bot.topic_manager import TopicManager


async def _run() -> None:
    load_dotenv()
    config = load_config()

    logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO))
    log = logging.getLogger("support_bot")

    db: Database | None = None
    bot: Bot | None = None
    admin_bridge: AdminSupportBridge | None = None
    bridge_task: asyncio.Task[None] | None = None
    bridge_stop = asyncio.Event()
    try:
        db = Database(config.db_path)
        await db.connect()
        await db.init()

        bot = Bot(
            token=config.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = Dispatcher()

        topics = TopicManager(db=db, operator_group_id=config.operator_group_id)

        dp["db"] = db
        dp["topics"] = topics
        dp["log_messages"] = config.log_messages
        dp["start_message"] = config.start_message
        if config.admin_bridge_enabled:
            admin_bridge = AdminSupportBridge(
                AdminBridgeSettings(
                    base_url=config.admin_bridge_url,
                    token=config.admin_bridge_token,
                    bot_instance_id=config.admin_bridge_bot_instance_id,
                    operator_group_id=config.operator_group_id,
                )
            )
            await admin_bridge.start()
            dp["admin_bridge"] = admin_bridge
            bridge_task = asyncio.create_task(
                admin_bridge.poll_outbox(bot, db, bridge_stop),
                name="admin-support-bridge",
            )
            log.info(
                "Admin support bridge enabled for bot instance %s",
                config.admin_bridge_bot_instance_id,
            )

        dp.include_router(user_router)

        operator_router.message.filter(F.chat.id == config.operator_group_id)
        operator_router.edited_message.filter(F.chat.id == config.operator_group_id)
        dp.include_router(operator_router)

        me = await bot.get_me()
        log.info("Started as @%s (id=%s)", me.username, me.id)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        bridge_stop.set()
        if bridge_task is not None:
            bridge_task.cancel()
            with suppress(asyncio.CancelledError):
                await bridge_task
        if admin_bridge is not None:
            await admin_bridge.close()
        if db is not None:
            await db.close()
        if bot is not None:
            await bot.session.close()


def main() -> None:
    asyncio.run(_run())

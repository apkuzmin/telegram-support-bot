from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from support_bot.config import load_config
from support_bot.omnichannel.files import LocalFileStore
from support_bot.omnichannel.realtime import RealtimeHub
from support_bot.omnichannel.service import SupportService
from support_bot.omnichannel.settings import OmnichannelSettings
from support_bot.omnichannel.storage import OmnichannelStore
from support_bot.omnichannel.telegram_bridge import (
    TelegramBridge,
    build_telegram_router,
)


async def _run() -> None:
    load_dotenv()
    telegram_config = load_config()
    settings = OmnichannelSettings.from_env()
    logging.basicConfig(
        level=getattr(
            logging,
            telegram_config.log_level.upper(),
            logging.INFO,
        )
    )
    store = OmnichannelStore(settings.database_url)
    if settings.environment == "development":
        await store.create_schema()
    bot = Bot(
        token=telegram_config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    stop_event = asyncio.Event()
    bridge = TelegramBridge(
        bot=bot,
        store=store,
        service=SupportService(store, RealtimeHub()),
        file_store=LocalFileStore(
            settings.upload_dir,
            max_bytes=settings.max_upload_bytes,
        ),
        operator_group_id=telegram_config.operator_group_id,
        start_message=telegram_config.start_message,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_telegram_router(bridge))
    outbox_task = asyncio.create_task(
        bridge.run_outbox_forever(stop_event=stop_event)
    )
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        stop_event.set()
        await outbox_task
        await store.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from support_bot.omnichannel.realtime import (
    PostgresRealtimeListener,
    RealtimeHub,
)


class _FakePostgresConnection:
    def __init__(self) -> None:
        self.closed = False
        self.callback = None

    async def add_listener(self, channel, callback) -> None:
        self.callback = callback
        callback(self, 1, channel, "1")

    async def remove_listener(self, channel, callback) -> None:
        self.callback = None

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class OmnichannelRealtimeTests(IsolatedAsyncioTestCase):
    async def test_postgres_notification_wakes_local_subscribers(self) -> None:
        hub = RealtimeHub()
        listener = PostgresRealtimeListener(
            "postgresql+asyncpg://support:password@db/support",
            hub,
        )
        connection = _FakePostgresConnection()
        stop_event = asyncio.Event()
        with patch(
            "support_bot.omnichannel.realtime.asyncpg.connect",
            new=AsyncMock(return_value=connection),
        ) as connect:
            async with hub.subscribe({"*"}) as queue:
                task = asyncio.create_task(listener.run(stop_event))
                signal = await asyncio.wait_for(queue.get(), timeout=1)
                self.assertEqual(signal["type"], "wake")
                stop_event.set()
                await task
        connect.assert_awaited_once_with(
            "postgresql://support:password@db/support"
        )
        self.assertTrue(connection.closed)

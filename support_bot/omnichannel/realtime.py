from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg


log = logging.getLogger(__name__)


class RealtimeHub:
    """In-process signal hub; reconnect catch-up always comes from the database."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(
        self, topics: set[str], *, queue_size: int = 100
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        async with self._lock:
            for topic in topics:
                self._subscribers[topic].add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                for topic in topics:
                    subscribers = self._subscribers.get(topic)
                    if subscribers is None:
                        continue
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(topic, None)

    async def publish(self, topics: set[str], event: dict[str, Any]) -> None:
        async with self._lock:
            queues = {
                queue
                for topic in topics
                for queue in self._subscribers.get(topic, ())
            }
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)


class PostgresRealtimeListener:
    def __init__(
        self,
        database_url: str,
        hub: RealtimeHub,
        *,
        channel: str = "support_realtime",
    ) -> None:
        self._dsn = database_url.replace(
            "postgresql+asyncpg://",
            "postgresql://",
            1,
        )
        self._hub = hub
        self._channel = channel

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            connection: asyncpg.Connection | None = None
            try:
                connection = await asyncpg.connect(self._dsn)

                def wake(*_: Any) -> None:
                    asyncio.create_task(
                        self._hub.publish({"*"}, {"type": "wake"})
                    )

                await connection.add_listener(self._channel, wake)
                await self._hub.publish({"*"}, {"type": "wake"})
                await stop_event.wait()
                await connection.remove_listener(self._channel, wake)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("PostgreSQL realtime listener failed")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
            finally:
                if connection is not None and not connection.is_closed():
                    await connection.close()

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


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

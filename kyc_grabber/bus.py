"""In-process pub/sub used to stream live progress to the dashboard."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

QUEUE_SIZE = 500


class EventBus:
    """Fan-out of job events to any number of SSE subscribers.

    ``publish`` is synchronous and thread safe so workers, the blocking IMAP
    reader and async pipeline code can all use the same bus.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._history: list[dict[str, Any]] = []
        self._history_limit = 200

    # ----------------------------------------------------------------- setup
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------- publishing
    def publish(self, event: dict[str, Any]) -> None:
        """Publish an event; safe from any thread."""
        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

        if self._loop is None or self._loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._fanout(event)
        else:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    # ------------------------------------------------------------ subscribing
    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(queue)
        for event in self._history[-50:]:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

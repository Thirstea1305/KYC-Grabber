"""Common lifecycle for every mail source: run loop, queue, health status."""

from __future__ import annotations

import abc
import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from ..logging_setup import get_logger
from ..models import RawMail

MailHandler = Callable[[RawMail], Awaitable[None]]

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BaseMailSource(abc.ABC):
    """A mail source pushes :class:`RawMail` objects to a handler.

    Subclasses only implement :meth:`_run`; everything else (lifecycle, queue,
    health reporting, simulated injection) lives here.
    """

    name = "base"

    def __init__(self, source_settings, handler: MailHandler) -> None:
        self.settings = source_settings
        self._handler = handler
        self._queue: asyncio.Queue[RawMail] = asyncio.Queue(maxsize=1000)
        self._run_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.running = False
        self.started_at: datetime | None = None
        self.last_poll_at: datetime | None = None
        self.last_mail_at: datetime | None = None
        self.mails_seen = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started_at = _now()
        self._loop = asyncio.get_running_loop()
        self._consumer_task = asyncio.create_task(self._consume(), name=f"{self.name}-consumer")
        self._run_task = asyncio.create_task(self._run(), name=f"{self.name}-source")
        logger.info("Mail source '%s' started", self.name)

    async def stop(self) -> None:
        self.running = False
        for task in (self._run_task, self._consumer_task):
            if task and not task.done():
                task.cancel()
        for task in (self._run_task, self._consumer_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown is best effort
                    pass
        self._run_task = None
        self._consumer_task = None
        logger.info("Mail source '%s' stopped", self.name)

    # ------------------------------------------------------------ submission
    async def submit(self, mail: RawMail) -> None:
        """Inject a mail (used by the dashboard's 'simulate inbound email' action)."""
        await self._queue.put(mail)
        self.mails_seen += 1
        self.last_mail_at = _now()

    async def _consume(self) -> None:
        while self.running:
            try:
                mail = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._handler(mail)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let one bad mail kill the consumer
                logger.exception("Failed to handle mail %s", mail.message_id)
                self.last_error = "handler failure"
            finally:
                self._queue.task_done()

    # --------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "name": self.name,
            "mode": getattr(self.settings, "mode", self.name),
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_poll_at": self.last_poll_at.isoformat() if self.last_poll_at else None,
            "last_mail_at": self.last_mail_at.isoformat() if self.last_mail_at else None,
            "mails_seen": self.mails_seen,
            "queue_depth": self._queue.qsize(),
            "last_error": self.last_error,
        }

    # --------------------------------------------------------------- abstract
    @abc.abstractmethod
    async def _run(self) -> None:
        """Source specific loop; must return or raise only when stopping."""

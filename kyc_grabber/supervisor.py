"""Supervisor: keeps the mail watcher, the worker pool and the dashboard alive.

This is what makes KYC Grabber an *always running* service:

* the mail source runs forever (IMAP IDLE in production, inbox polling in dev),
* a worker pool drains the job queue,
* the web dashboard is served on the same event loop,
* SIGINT/SIGTERM trigger a graceful shutdown,
* a housekeeping task trims the event log so the database does not grow forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from datetime import datetime, timezone

import uvicorn

from .logging_setup import get_logger
from .runtime import AppContext
from .web.app import create_app

logger = get_logger(__name__)

HOUSEKEEPING_INTERVAL_SECONDS = 300


class Supervisor:
    def __init__(self, context: AppContext, *, with_web: bool = True, with_source: bool = True) -> None:
        self.context = context
        self.with_web = with_web
        self.with_source = with_source
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: uvicorn.Server | None = None
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ control
    def request_stop(self) -> None:
        """Ask the supervisor to stop; safe to call from any thread."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._stop.set)
        else:  # pragma: no cover - only before run() starts
            self._stop.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        def _handler(signum, _frame) -> None:  # noqa: ANN001
            logger.info("Received signal %s - shutting down gracefully", signum)
            loop.call_soon_threadsafe(self._stop.set)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError, AttributeError):  # pragma: no cover - platform dependent
                logger.debug("Cannot install handler for %s", sig)

    # --------------------------------------------------------------------- run
    async def run(self) -> None:
        context = self.context
        self._loop = asyncio.get_running_loop()
        context.bus.bind_loop(self._loop)
        self._install_signal_handlers()

        await context.worker.start()
        if self.with_source:
            await context.source.start()
        if self.with_web:
            self._tasks.append(asyncio.create_task(self._serve_web(), name="web"))
        self._tasks.append(asyncio.create_task(self._housekeeping(), name="housekeeping"))

        logger.info(
            "KYC Grabber is up - dashboard on http://%s:%d (mail mode=%s)",
            context.settings.web.host, context.settings.web.port, context.source.name,
        )

        try:
            await self._stop.wait()
        finally:
            await self.shutdown()

    async def _serve_web(self) -> None:
        settings = self.context.settings
        config = uvicorn.Config(
            create_app(self.context),
            host=settings.web.host,
            port=settings.web.port,
            log_level=settings.log_level.lower(),
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        # The supervisor owns signal handling.
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._server = server
        try:
            await server.serve()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - e.g. port already in use
            logger.exception("Web server stopped unexpectedly")

    async def _housekeeping(self) -> None:
        while True:
            try:
                await asyncio.sleep(HOUSEKEEPING_INTERVAL_SECONDS)
                await asyncio.to_thread(self.context.store.prune_events, 5000)
                status = self.context.status()
                logger.debug(
                    "Heartbeat %s | mail=%s inbox_polled=%s jobs=%s queue=%s",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    status["source"]["running"],
                    status["source"]["last_poll_at"],
                    status["data"]["jobs"],
                    status["worker"]["queued"],
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - housekeeping must never crash the service
                logger.exception("Housekeeping iteration failed")

    # ----------------------------------------------------------------- shutdown
    async def shutdown(self) -> None:
        logger.info("Shutting down KYC Grabber...")
        if self._server is not None:
            self._server.should_exit = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []

        with contextlib.suppress(Exception):
            await self.context.source.stop()
        with contextlib.suppress(Exception):
            await self.context.worker.stop()
        logger.info("Stopped.")


def run(context: AppContext, *, with_web: bool = True, with_source: bool = True) -> None:
    supervisor = Supervisor(context, with_web=with_web, with_source=with_source)
    try:
        asyncio.run(supervisor.run())
    except KeyboardInterrupt:  # pragma: no cover - Ctrl+C
        logger.info("Interrupted by user")

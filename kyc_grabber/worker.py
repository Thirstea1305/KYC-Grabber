"""Bounded pool of job workers consuming the queue filled by the intake step."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .logging_setup import get_logger
from .pipeline import KycPipeline

logger = get_logger(__name__)


class JobWorker:
    def __init__(self, pipeline: KycPipeline, count: int = 2, queue_size: int = 500) -> None:
        self.pipeline = pipeline
        self.count = max(1, count)
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self._tasks: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self.running = False
        self.started_at: datetime | None = None
        self.processed = 0
        self.failed = 0
        self.active = 0

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started_at = datetime.now(timezone.utc)
        self._loop = asyncio.get_running_loop()
        self._tasks = [
            asyncio.create_task(self._worker(index), name=f"kyc-worker-{index}")
            for index in range(self.count)
        ]
        logger.info("Job worker pool started with %d worker(s)", self.count)

    async def stop(self) -> None:
        self.running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown is best effort
                pass
        self._tasks = []
        logger.info("Job worker pool stopped")

    # ------------------------------------------------------------ submission
    def enqueue_threadsafe(self, job_id: str) -> None:
        """Enqueue a job from a non-async thread (used by the IMAP reader)."""
        if self._loop is None:  # pragma: no cover - defensive
            raise RuntimeError("Worker pool is not running")
        self._loop.call_soon_threadsafe(self._queue.put_nowait, job_id)

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    # --------------------------------------------------------------- internals
    async def _worker(self, index: int) -> None:
        while self.running:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                raise
            self.active += 1
            try:
                await self.pipeline.process_job(job_id)
                self.processed += 1
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad job must not stop the pool
                self.failed += 1
                logger.exception("Worker %d failed on job %s", index, job_id)
            finally:
                self.active -= 1
                self._queue.task_done()

    def status(self) -> dict:
        return {
            "running": self.running,
            "workers": self.count,
            "active": self.active,
            "queued": self._queue.qsize(),
            "processed": self.processed,
            "failed": self.failed,
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }

"""Application context: one object wiring every component together.

Both the long-running supervisor and the API layer (and the tests) build the
context the same way, so what the dashboard reports is exactly what the service
is doing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .bus import EventBus
from .clients import ExternalDatabaseClient, InternalDatabaseClient
from .config import Settings
from .logging_setup import get_logger
from .mail.sender import OutboundMailer
from .pipeline import KycPipeline
from .store import Store
from .watch import BaseMailSource, MailFilter, build_source
from .worker import JobWorker

logger = get_logger(__name__)


@dataclass
class AppContext:
    settings: Settings
    store: Store
    bus: EventBus
    internal: InternalDatabaseClient
    external: ExternalDatabaseClient
    outbound: OutboundMailer
    mail_filter: MailFilter
    pipeline: KycPipeline
    source: BaseMailSource
    worker: JobWorker
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ views
    def job_view(self, job_id: str) -> dict | None:
        job = self.store.get_job(job_id)
        if job is None:
            return None
        view = dict(job)
        view["items"] = self.store.list_items(job_id)
        view["events"] = self.store.list_events(limit=100, job_id=job_id)
        view["progress"] = round(
            (job["processed_codes"] / job["total_codes"] * 100) if job["total_codes"] else 100.0, 1
        )
        return view

    def list_job_views(self, limit: int = 50) -> list[dict]:
        views = []
        for job in self.store.list_jobs(limit=limit):
            view = dict(job)
            view["progress"] = round(
                (job["processed_codes"] / job["total_codes"] * 100) if job["total_codes"] else 100.0, 1
            )
            views.append(view)
        return views

    def status(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round((datetime.now(timezone.utc) - self.started_at).total_seconds(), 1),
            "started_at": self.started_at.isoformat(),
            "environment": self.settings.app_env,
            "config": self.settings.redacted(),
            "source": self.source.status(),
            "worker": self.worker.status(),
            "watchers": {"subscribers": self.bus.subscriber_count},
            "data": self.pipeline.stats(),
            "server_time": datetime.now(timezone.utc).isoformat(),
        }

    def close(self) -> None:
        self.store.close()


def build_context(settings: Settings, *, source_mode: str | None = None) -> AppContext:
    """Construct every component in dependency order."""
    settings.ensure_directories()

    store = Store(settings.store_file)
    bus = EventBus()
    internal = InternalDatabaseClient(settings.internal, settings.internal_seed_file)
    internal.load()
    external = ExternalDatabaseClient(settings.external)
    outbound = OutboundMailer(settings.outbound, settings.outbox_path)
    mail_filter = MailFilter(settings.filter)

    pipeline = KycPipeline(
        settings=settings,
        store=store,
        bus=bus,
        internal=internal,
        external=external,
        outbound=outbound,
        mail_filter=mail_filter,
    )

    if source_mode:
        settings.mail.mode = source_mode  # type: ignore[assignment]
    source = build_source(settings.mail, pipeline.ingest)
    worker = JobWorker(pipeline, count=settings.pipeline.workers)
    # Every accepted email becomes a job in the worker queue.
    pipeline.job_sink = worker.enqueue

    logger.info(
        "Context ready (mail mode=%s, outbound=%s, workers=%d, internal records=%d)",
        source.name, settings.outbound.mode, settings.pipeline.workers, internal.record_count,
    )
    return AppContext(
        settings=settings,
        store=store,
        bus=bus,
        internal=internal,
        external=external,
        outbound=outbound,
        mail_filter=mail_filter,
        pipeline=pipeline,
        source=source,
        worker=worker,
    )

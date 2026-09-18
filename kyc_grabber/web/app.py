"""FastAPI application exposing the KYC Grabber dashboard and its API.

Endpoints
---------
GET  /                             operations dashboard (static SPA)
GET  /api/health                   liveness probe
GET  /api/status                   watcher / worker / data-source health
GET  /api/jobs                     recent jobs
GET  /api/jobs/{job_id}            job detail incl. per-third-party results
GET  /api/jobs/{job_id}/workbook   download the generated Excel report
POST /api/jobs/{job_id}/rerun      re-queue a job
GET  /api/events                   recent events (REST)
GET  /api/events/stream            live events (Server-Sent Events)
POST /api/simulate/email           inject a synthetic inbound email (JSON)
POST /api/simulate/email/upload    inject a synthetic inbound email with a CSV file
GET  /api/sample/codes.csv         sample CSV payload for demos
POST /api/external/cache/clear     drop the simulated external cache
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..logging_setup import get_logger
from ..mail.composer import compose_message, parse_rfc822
from ..models import JobStatus
from ..runtime import AppContext
from ..samples import SAMPLE_CSV, SAMPLE_FILENAME, SAMPLE_SUBJECT

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class SimulateEmailRequest(BaseModel):
    sender: str = Field(default="analyst@example.com")
    subject: str = Field(default=SAMPLE_SUBJECT)
    filename: str = Field(default=SAMPLE_FILENAME)
    csv_text: str = Field(default=SAMPLE_CSV)
    body: str = Field(default="Please run the standard KYC checks on the attached list.")
    message_id: str | None = None


def create_app(context: AppContext) -> FastAPI:
    app = FastAPI(title=context.settings.web.title, version=__version__, docs_url="/api/docs")
    app.state.context = context

    # ------------------------------------------------------------------ static
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/static/{filename:path}", include_in_schema=False)
    async def static_file(filename: str):
        target = (STATIC_DIR / filename).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(target)

    # -------------------------------------------------------------------- meta
    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__, "environment": context.settings.app_env}

    @app.get("/api/status")
    async def status() -> dict:
        return context.status()

    @app.get("/api/sample/codes.csv", include_in_schema=False)
    async def sample_csv() -> PlainTextResponse:
        return PlainTextResponse(SAMPLE_CSV, media_type="text/csv")

    # -------------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    async def list_jobs(limit: int = 25) -> dict:
        jobs = context.list_job_views(limit=max(1, min(limit, 200)))
        return {"jobs": jobs, "counts": context.store.job_counts()}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        view = context.job_view(job_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"Unknown job {job_id}")
        return view

    @app.get("/api/jobs/{job_id}/workbook")
    async def download_workbook(job_id: str):
        job = context.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job {job_id}")
        if not job.get("workbook_path"):
            raise HTTPException(status_code=404, detail="This job has no workbook yet")
        path = Path(job["workbook_path"])
        if not path.is_file():
            raise HTTPException(status_code=410, detail=f"Workbook missing on disk: {path}")
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=path.name,
        )

    @app.post("/api/jobs/{job_id}/rerun")
    async def rerun_job(job_id: str) -> dict:
        job = context.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job {job_id}")
        if not job["total_codes"]:
            raise HTTPException(status_code=400, detail="Nothing to re-run for this job")
        context.store.update_job(job_id, status=JobStatus.QUEUED.value, error=None)
        await context.worker.enqueue(job_id)
        context.store.add_event(f"Job {job_id} re-queued by operator", job_id=job_id)
        return {"queued": True, "job_id": job_id}

    # ------------------------------------------------------------------ events
    @app.get("/api/events")
    async def list_events(limit: int = 200, job_id: str | None = None) -> dict:
        return {"events": context.store.list_events(limit=max(1, min(limit, 1000)), job_id=job_id)}

    @app.get("/api/events/stream", include_in_schema=False)
    async def event_stream(request: Request) -> StreamingResponse:
        queue = await context.bus.subscribe()

        async def generator():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except (TimeoutError, asyncio.TimeoutError):
                        yield ": keep-alive\n\n"
                        continue
                    payload = {
                        "id": event.get("id"),
                        "job_id": event.get("job_id"),
                        "at": event.get("at"),
                        "level": event.get("level", "info"),
                        "message": event.get("message", ""),
                        "payload": event.get("payload") or {},
                    }
                    yield f"event: log\ndata: {json.dumps(payload)}\n\n"
            except asyncio.CancelledError:
                raise
            finally:
                await context.bus.unsubscribe(queue)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------------------------------------------------------------- simulate
    @app.post("/api/simulate/email")
    async def simulate_email(payload: SimulateEmailRequest = Body(...)) -> dict:
        message = compose_message(
            sender=payload.sender,
            to=context.settings.outbound.from_address,
            subject=payload.subject,
            body_text=payload.body,
            attachments=[(payload.filename, payload.csv_text.encode("utf-8"))],
            message_id=payload.message_id,
        )
        return await _deliver(context, bytes(message), payload.sender)

    @app.post("/api/simulate/email/upload")
    async def simulate_email_upload(
        sender: str = Form("analyst@example.com"),
        subject: str = Form(SAMPLE_SUBJECT),
        file: UploadFile = File(...),
    ) -> dict:
        content = await file.read()
        message = compose_message(
            sender=sender,
            to=context.settings.outbound.from_address,
            subject=subject,
            body_text="Attached list of third-party codes.",
            attachments=[(file.filename or "codes.csv", content)],
        )
        return await _deliver(context, bytes(message), sender)

    # ----------------------------------------------------------- integrations
    @app.post("/api/external/cache/clear")
    async def clear_external_cache() -> dict:
        size = context.external.stats["cached_codes"]
        context.external.clear_cache()
        context.store.add_event(f"External cache cleared ({size} entries)")
        return {"cleared": size}

    @app.get("/api/inbox")
    async def inbox_state() -> dict:
        inbox = context.settings.inbox_path
        processed = context.settings.processed_path
        return {
            "inbox_dir": str(inbox),
            "pending": sorted(p.name for p in inbox.glob("*.eml")) if inbox.exists() else [],
            "processed_count": len(list(processed.glob("*.eml"))) if processed.exists() else 0,
        }

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    return app


async def _deliver(context: AppContext, raw: bytes, sender: str) -> dict:
    """Route a simulated email through the configured intake path."""
    if context.settings.mail.mode == "filesystem":
        inbox = context.settings.inbox_path
        inbox.mkdir(parents=True, exist_ok=True)
        mail = parse_rfc822(raw, source="simulate")
        target = inbox / f"{mail.message_id.replace('/', '_')[:40]}.eml"
        target.write_bytes(raw)
        logger.info("Simulated email dropped into %s", target)
        return {
            "accepted": True,
            "route": "filesystem-inbox",
            "detail": f"Email written to {target.name}; the watcher will pick it up on its next poll.",
            "path": str(target),
        }

    mail = parse_rfc822(raw, source="simulate")
    await context.source.submit(mail)
    return {
        "accepted": True,
        "route": "direct-injection",
        "detail": f"Email injected into the {context.source.name} source queue.",
    }

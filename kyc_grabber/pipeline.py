"""The KYC pipeline: inbound email -> codes -> enrichment -> workbook -> reply."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from .analysis import compare, derive_risk
from .clients import ExternalDatabaseClient, ExternalLookupError, InternalDatabaseClient
from .config import Settings
from .csv_parser import parse_codes
from .excel_report import write_workbook
from .logging_setup import get_logger
from .mail.composer import render_rejection_body, render_result_body
from .mail.sender import OutboundMailer
from .models import (
    ExternalRecord,
    InternalRecord,
    ItemResult,
    ItemStatus,
    JobStatus,
    MatchStatus,
    RawMail,
    RiskRating,
)
from .store import Store, new_job_id
from .bus import EventBus
from .watch.filter import MailFilter

logger = get_logger(__name__)


class KycPipeline:
    """Orchestrates the four steps of the KYC Grabber workflow.

    1. Filter and parse the inbound email + CSV attachment(s).
    2. Look every code up in the internal database.
    3. Look every code up in the external database.
    4. Render a multi-sheet workbook and reply to the requester.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        store: Store,
        bus: EventBus,
        internal: InternalDatabaseClient,
        external: ExternalDatabaseClient,
        outbound: OutboundMailer,
        mail_filter: MailFilter,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bus = bus
        self.internal = internal
        self.external = external
        self.outbound = outbound
        self.mail_filter = mail_filter
        self._semaphore = asyncio.Semaphore(max(1, settings.pipeline.code_concurrency))
        # Set by the runtime once the worker pool exists; the CLI's one-shot mode
        # leaves it empty and processes the job inline instead.
        self.job_sink: Callable[[str], Awaitable[None]] | None = None

    # ------------------------------------------------------------------ events
    def _emit(self, message: str, *, level: str = "info", job_id: str | None = None, payload: dict | None = None) -> None:
        event = self.store.add_event(message, level=level, job_id=job_id, payload=payload)
        self.bus.publish(event)

    # ------------------------------------------------------------------ step 1
    async def ingest(self, mail: RawMail) -> str | None:
        """Handle one inbound email. Returns the created job id, if any."""
        if self.store.already_seen(mail.message_id):
            self._emit(
                "Duplicate email ignored",
                level="warn",
                payload={"message_id": mail.message_id, "sender": mail.sender},
            )
            return None

        self._emit(
            "Email received",
            payload={"sender": mail.sender, "subject": mail.subject, "source": mail.source,
                     "attachments": [a.filename for a in mail.attachments]},
        )

        decision = self.mail_filter.evaluate(mail)
        if not decision.accepted:
            self._reject(mail, decision.reason)
            return None

        codes: list[str] = []
        parse_errors: list[str] = []
        duplicates = 0
        for attachment in decision.csv_attachments:
            outcome = parse_codes(attachment.content, attachment.filename)
            parse_errors.extend(outcome.errors)
            duplicates += outcome.duplicates
            codes.extend(item.code for item in outcome.codes)

        # De-duplicate across multiple attachments, keeping first-seen order.
        seen: set[str] = set()
        ordered: list[str] = []
        for code in codes:
            if code not in seen:
                seen.add(code)
                ordered.append(code)

        if not ordered:
            self._reject(mail, "No valid third-party codes found. " + " ".join(parse_errors[:3]))
            return None

        limit = self.settings.filter.max_codes_per_request
        if len(ordered) > limit:
            self._reject(mail, f"{len(ordered)} codes requested but the limit per email is {limit}.")
            return None

        job_id = new_job_id()
        filename = ", ".join(a.filename for a in decision.csv_attachments)
        self.store.create_job(
            job_id=job_id,
            sender=mail.sender,
            subject=mail.subject,
            message_id=mail.message_id,
            filename=filename,
            source=mail.source,
            total_codes=len(ordered),
            status=JobStatus.QUEUED.value,
            raw_mail_path=mail.raw_path,
        )
        for code in ordered:
            self.store.upsert_item(job_id, code, status=ItemStatus.PENDING.value)
        self.store.mark_seen(mail.message_id, job_id)

        self._emit(
            f"Job {job_id} queued with {len(ordered)} third-party codes"
            + (f" ({duplicates} duplicate rows skipped)" if duplicates else ""),
            job_id=job_id,
            payload={
                "codes": ordered,
                "filename": filename,
                "parse_errors": parse_errors[:10],
                "duplicates": duplicates,
            },
        )
        await self._dispatch(job_id)
        return job_id

    async def _dispatch(self, job_id: str) -> None:
        """Hand a freshly created job to the worker pool."""
        if self.job_sink is None:
            logger.debug("No job sink configured - job %s must be processed explicitly", job_id)
            return
        await self.job_sink(job_id)

    def _reject(self, mail: RawMail, reason: str, *, notify: bool = True) -> None:
        job_id = new_job_id()
        self.store.create_job(
            job_id=job_id,
            sender=mail.sender,
            subject=mail.subject,
            message_id=mail.message_id,
            filename=", ".join(a.filename for a in mail.attachments),
            source=mail.source,
            total_codes=0,
            status=JobStatus.REJECTED.value,
            raw_mail_path=mail.raw_path,
        )
        self.store.update_job(job_id, reject_reason=reason)
        self.store.mark_seen(mail.message_id, job_id)
        self._emit(f"Email rejected: {reason}", level="warn", job_id=job_id,
                   payload={"sender": mail.sender, "subject": mail.subject})

        if notify and self.settings.filter.reply_on_reject and mail.sender:
            asyncio.create_task(self._notify_rejection(mail, reason, job_id))

    async def _notify_rejection(self, mail: RawMail, reason: str, job_id: str) -> None:
        try:
            delivery = await self.outbound.send_reply(
                to=mail.sender,
                subject=f"Re: {mail.subject}" if mail.subject else "KYC Grabber: request rejected",
                body_text=render_rejection_body(reason=reason, subject=mail.subject),
                job_id=job_id,
            )
            self.store.update_job(job_id, delivered_to=delivery.target, delivery_mode=delivery.mode)
        except Exception as exc:  # noqa: BLE001 - notification failure is not fatal
            logger.warning("Could not send rejection notice for %s: %s", job_id, exc)

    # ------------------------------------------------------------------ step 2-4
    async def process_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            logger.error("Job %s disappeared before processing", job_id)
            return

        codes = [item["code"] for item in self.store.list_items(job_id)]
        try:
            self.store.update_job(job_id, status=JobStatus.ENRICHING.value)
            self._emit(f"Enrichment started for {len(codes)} third parties", job_id=job_id,
                       payload={"total": len(codes)})

            results = await self._enrich_all(job_id, codes)

            self.store.update_job(job_id, status=JobStatus.RENDERING.value)
            self._emit("Building Excel report (one sheet per third party)", job_id=job_id)
            path = await asyncio.to_thread(
                write_workbook,
                job_id=job_id,
                sender=job["sender"],
                subject=job["subject"],
                source_file=job["filename"],
                items=results,
                settings=self.settings.excel,
                output_dir=self.settings.output_path,
            )
            self.store.update_job(job_id, workbook_path=str(path))
            self._emit(f"Workbook written: {path.name}", job_id=job_id, payload={"path": str(path)})

            self.store.update_job(job_id, status=JobStatus.DELIVERING.value)
            warnings = self._summarize(results)
            delivery = await self.outbound.send_reply(
                to=job["sender"],
                subject=self.settings.outbound.subject_template.format(
                    filename=job["filename"] or "request", count=len(results)
                ),
                body_text=render_result_body(job_id=job_id, items=results, filename=path.name, warnings=warnings),
                attachments=[(path.name, path.read_bytes())],
                job_id=job_id,
            )
            self.store.update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                delivered_to=delivery.target,
                delivery_mode=delivery.mode,
            )
            self._emit(
                f"Job {job_id} completed - report delivered to {delivery.target} via {delivery.mode}",
                job_id=job_id,
                payload={"workbook": str(path), "warnings": warnings},
            )
        except asyncio.CancelledError:
            self.store.update_job(job_id, status=JobStatus.FAILED.value, error="cancelled during shutdown")
            raise
        except Exception as exc:  # noqa: BLE001 - report the failure to the requester
            logger.exception("Job %s failed", job_id)
            self.store.update_job(job_id, status=JobStatus.FAILED.value, error=f"{type(exc).__name__}: {exc}")
            self._emit(f"Job {job_id} failed: {exc}", level="error", job_id=job_id)

    async def _enrich_all(self, job_id: str, codes: list[str]) -> list[ItemResult]:
        results: list[ItemResult] = []
        total = len(codes)
        for index, code in enumerate(codes, start=1):
            result = await self._enrich_code(code)
            results.append(result)
            self.store.upsert_item(
                job_id,
                code,
                status=result.status.value,
                match_status=result.match_status.value if result.match_status else None,
                risk_rating=result.risk_rating.value,
                differences=result.differences,
                warnings=result.warnings,
                error=result.error,
                duration_ms=result.duration_ms,
                internal_json=result.internal.model_dump(mode="json") if result.internal else None,
                external_json=result.external.model_dump(mode="json") if result.external else None,
            )
            failed = 1 if result.status is ItemStatus.FAILED else 0
            warned = 1 if (result.warnings or result.differences) else 0
            self.store.increment_job_counters(job_id, processed=1, failed=failed, warnings=warned)
            self._emit(
                f"[{index}/{total}] {code}: {result.match_status.value if result.match_status else 'error'}"
                f" (risk={result.risk_rating.value})",
                level="warn" if result.error else "info",
                job_id=job_id,
                payload={
                    "code": code,
                    "processed": index,
                    "total": total,
                    "match_status": result.match_status.value if result.match_status else None,
                    "risk": result.risk_rating.value,
                    "differences": result.differences,
                    "error": result.error,
                },
            )
        return results

    async def _enrich_code(self, code: str) -> ItemResult:
        """Internal lookup, then external lookup, then reconcile the two."""
        started = perf_counter()
        async with self._semaphore:
            internal_record, internal_error = await self._lookup_internal(code)
            external_record, external_error = await self._lookup_external(code, internal_record)

        match_status, differences, warnings = compare(internal_record, external_record)
        risk: RiskRating = derive_risk(internal_record, external_record)

        status = ItemStatus.COMPLETED
        error: str | None = None
        if internal_error or external_error:
            status = ItemStatus.FAILED
            match_status = MatchStatus.ERROR
            error = "; ".join(filter(None, [internal_error, external_error]))
            warnings.append("Source lookup failed - result is incomplete, manual follow-up required.")

        return ItemResult(
            code=code,
            status=status,
            match_status=match_status,
            internal=internal_record,
            external=external_record,
            risk_rating=risk,
            differences=differences,
            warnings=warnings,
            error=error,
            duration_ms=int((perf_counter() - started) * 1000),
        )

    async def _lookup_internal(self, code: str) -> tuple[InternalRecord | None, str | None]:
        try:
            record = await asyncio.wait_for(
                self.internal.lookup(code), timeout=self.settings.pipeline.code_timeout_seconds
            )
            return record, None
        except (TimeoutError, asyncio.TimeoutError):
            return None, "internal lookup timed out"
        except Exception as exc:  # noqa: BLE001 - never fail the whole job for one code
            logger.warning("Internal lookup failed for %s: %s", code, exc)
            return None, f"internal lookup error: {exc}"

    async def _lookup_external(
        self, code: str, internal_record: InternalRecord | None
    ) -> tuple[ExternalRecord | None, str | None]:
        try:
            record = await asyncio.wait_for(
                self.external.fetch(
                    code,
                    # Simulation hint: the fake registry usually mirrors the internal
                    # record so the reconciliation logic has something meaningful to
                    # compare. Drop these arguments for a real provider.
                    expected_name=internal_record.legal_name if internal_record else None,
                    expected_country=internal_record.country if internal_record else None,
                    expected_entity_type=internal_record.entity_type if internal_record else None,
                ),
                timeout=self.settings.pipeline.code_timeout_seconds,
            )
            return record, None
        except (TimeoutError, asyncio.TimeoutError):
            return None, "external lookup timed out"
        except ExternalLookupError as exc:
            return None, str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("External lookup failed for %s: %s", code, exc)
            return None, f"external lookup error: {exc}"

    # ---------------------------------------------------------------- reporting
    @staticmethod
    def _summarize(results: list[ItemResult]) -> list[str]:
        counts: dict[str, int] = {}
        for result in results:
            key = result.match_status.value if result.match_status else "error"
            counts[key] = counts.get(key, 0) + 1

        warnings: list[str] = []
        if counts.get("mismatch"):
            warnings.append(f"{counts['mismatch']} third part(ies) have data discrepancies between sources.")
        if counts.get("not_found"):
            warnings.append(f"{counts['not_found']} third part(ies) were not found in either source.")
        if counts.get("external_only"):
            warnings.append(f"{counts['external_only']} third part(ies) are missing from the internal master data.")
        if counts.get("internal_only"):
            warnings.append(f"{counts['internal_only']} third part(ies) have no external registry record.")
        if counts.get("error"):
            warnings.append(f"{counts['error']} third part(ies) could not be fully checked - see the workbook.")
        critical = [r.code for r in results if r.risk_rating is RiskRating.CRITICAL]
        if critical:
            warnings.append(f"CRITICAL risk flags raised for: {', '.join(critical)}")
        return warnings

    def stats(self) -> dict[str, Any]:
        return {
            "internal_records": self.internal.record_count,
            "external": self.external.stats,
            "jobs": self.store.job_counts(),
        }

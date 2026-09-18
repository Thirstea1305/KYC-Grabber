"""Shared fixtures/helpers for the test suite (plain functions, no plugins needed)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

from kyc_grabber.config import (
    ExcelSettings,
    ExternalSettings,
    FilterSettings,
    InternalSettings,
    MailSettings,
    OutboundSettings,
    PipelineSettings,
    Settings,
    WebSettings,
)
from kyc_grabber.models import Attachment, RawMail

SEED = {
    "records": [
        {"code": "TP-0001", "legal_name": "Aurora Logistics Ltd", "entity_type": "Private Limited Company",
         "country": "GB", "risk_rating": "low", "status": "active", "sanctions_hits": 0, "pep_flags": 0},
        {"code": "TP-0002", "legal_name": "Meridian Trading GmbH", "entity_type": "GmbH",
         "country": "DE", "risk_rating": "medium", "status": "active", "sanctions_hits": 0, "pep_flags": 0},
        {"code": "TP-0003", "legal_name": "Northwind Systems B.V.", "entity_type": "B.V.",
         "country": "NL", "risk_rating": "high", "status": "active", "sanctions_hits": 0, "pep_flags": 1},
    ]
}


def make_settings(tmp_path: Path) -> Settings:
    """Fully isolated settings pointing at a temporary directory."""
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(SEED), encoding="utf-8")
    data_dir = tmp_path / "data"

    return Settings(
        _env_file=None,
        app_env="dev",
        log_level="WARNING",
        data_dir=data_dir,
        store_path=data_dir / "store.sqlite3",
        web=WebSettings(host="127.0.0.1", port=0),
        mail=MailSettings(
            mode="filesystem",
            inbox_dir=data_dir / "inbox",
            processed_dir=data_dir / "inbox" / "processed",
            poll_interval_seconds=0.05,
        ),
        filter=FilterSettings(allowed_senders="*", subject_token="[KYC]", reply_on_reject=False),
        outbound=OutboundSettings(mode="filesystem", outbox_dir=data_dir / "outbox",
                                  from_address="kyc-bot@example.com"),
        internal=InternalSettings(latency_ms=0, seed_path=seed_path),
        external=ExternalSettings(latency_ms=0, jitter_ms=0, failure_rate=0.0, not_found_rate=0.0,
                                  max_retries=1, retry_backoff_seconds=0.0, concurrency=4),
        pipeline=PipelineSettings(workers=1, code_concurrency=4, code_timeout_seconds=5.0),
        excel=ExcelSettings(output_dir=data_dir / "out", include_raw_payload=False),
    )


def make_mail(csv_text: str = "third_party_code\nTP-0001\n", filename: str = "third_parties.csv",
              message_id: str = "<m1@corp.com>", subject: str = "[KYC] screening") -> RawMail:
    return RawMail(
        message_id=message_id,
        sender="analyst@corp.com",
        subject=subject,
        body_text="please screen",
        attachments=[Attachment(filename=filename, content_type="text/csv", content=csv_text.encode())],
    )


async def wait_for(predicate: Callable[[], object], timeout: float = 15.0, interval: float = 0.05):
    """Poll *predicate* until it returns a truthy value."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    raise AssertionError("condition not met before timeout")


def run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]

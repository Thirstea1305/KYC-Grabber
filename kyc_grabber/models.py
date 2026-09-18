"""Domain models shared across intake, enrichment and reporting."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    RECEIVED = "received"
    QUEUED = "queued"
    ENRICHING = "enriching"
    RENDERING = "rendering"
    DELIVERING = "delivering"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REJECTED}


class ItemStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MatchStatus(str, Enum):
    """Outcome of reconciling the internal and external record for one code."""

    MATCH = "match"
    MISMATCH = "mismatch"
    INTERNAL_ONLY = "internal_only"
    EXTERNAL_ONLY = "external_only"
    NOT_FOUND = "not_found"
    ERROR = "error"


class RiskRating(str, Enum):
    UNRATED = "unrated"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_ORDER: dict[RiskRating, int] = {
    RiskRating.UNRATED: 0,
    RiskRating.LOW: 1,
    RiskRating.MEDIUM: 2,
    RiskRating.HIGH: 3,
    RiskRating.CRITICAL: 4,
}


def _coerce_risk(value: RiskRating | str | None) -> RiskRating:
    """Best-effort conversion to RiskRating.

    Note: ``RiskRating`` is a ``str`` mixin enum, so ``str(member)`` yields
    ``'RiskRating.LOW'`` rather than ``'low'`` - members must be handled first.
    """
    if isinstance(value, RiskRating):
        return value
    if value is None:
        return RiskRating.UNRATED
    text = str(getattr(value, "value", value)).strip().lower()
    try:
        return RiskRating(text)
    except ValueError:
        return RiskRating.UNRATED


def max_risk(*ratings: RiskRating | str | None) -> RiskRating:
    best = RiskRating.UNRATED
    for rating in ratings:
        candidate = _coerce_risk(rating)
        if RISK_ORDER[candidate] > RISK_ORDER[best]:
            best = candidate
    return best


class Attachment(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    content: bytes = b""


class RawMail(BaseModel):
    """Normalised representation of an inbound email, whatever the source."""

    source: str = "filesystem"
    message_id: str
    sender: str
    subject: str = ""
    received_at: datetime = Field(default_factory=utcnow)
    body_text: str = ""
    attachments: list[Attachment] = Field(default_factory=list)
    raw_path: str | None = None


class ThirdPartyCode(BaseModel):
    code: str
    row_number: int = 0
    source_file: str = ""


class ParseOutcome(BaseModel):
    codes: list[ThirdPartyCode] = Field(default_factory=list)
    duplicates: int = 0
    errors: list[str] = Field(default_factory=list)
    source_file: str = ""


class InternalRecord(BaseModel):
    """Row from the (simulated) internal third-party master data."""

    code: str
    legal_name: str
    entity_type: str
    country: str
    risk_rating: RiskRating = RiskRating.UNRATED
    status: str = "active"
    onboarded_at: str | None = None
    business_unit: str | None = None
    sanctions_hits: int = 0
    pep_flags: int = 0
    notes: str | None = None
    source: str = "internal-master-data (simulated)"


class ExternalRecord(BaseModel):
    """Payload from the (simulated) external registry / screening provider."""

    code: str
    registry_name: str
    registration_number: str
    legal_name: str
    entity_type: str
    country: str
    incorporation_date: str | None = None
    status: str = "active"
    registered_address: str | None = None
    directors: list[str] = Field(default_factory=list)
    shareholders: list[str] = Field(default_factory=list)
    sanctions_listed: bool = False
    sanctions_lists: list[str] = Field(default_factory=list)
    pep_match: bool = False
    adverse_media_count: int = 0
    adverse_media: list[str] = Field(default_factory=list)
    source_url: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)
    source: str = "simulated-registry (SYNTHETIC DEMO DATA)"


class ItemResult(BaseModel):
    code: str
    status: ItemStatus = ItemStatus.PENDING
    match_status: MatchStatus | None = None
    internal: InternalRecord | None = None
    external: ExternalRecord | None = None
    risk_rating: RiskRating = RiskRating.UNRATED
    differences: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class DeliveryInfo(BaseModel):
    mode: str
    target: str
    path: str | None = None
    sent_at: datetime = Field(default_factory=utcnow)


class JobView(BaseModel):
    id: str
    status: JobStatus
    source: str = "filesystem"
    sender: str = ""
    subject: str = ""
    message_id: str = ""
    filename: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    total_codes: int = 0
    processed_codes: int = 0
    failed_codes: int = 0
    warnings: int = 0
    error: str | None = None
    reject_reason: str | None = None
    workbook_path: str | None = None
    delivered_to: str | None = None
    delivery_mode: str | None = None
    items: list[ItemResult] = Field(default_factory=list)


class EventView(BaseModel):
    id: int | None = None
    job_id: str | None = None
    at: datetime = Field(default_factory=utcnow)
    level: str = "info"
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

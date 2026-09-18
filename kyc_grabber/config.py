"""Application configuration.

All settings are environment driven and prefixed with ``KYC_``.
Nested sections use a double underscore, e.g. ``KYC_MAIL__IMAP__HOST``.

Nested sections are declared as plain models (not ``BaseSettings``) so that the
whole tree can be loaded from env vars *and* overridden in tests by simply
building ``Settings(mail=MailSettings(mode="filesystem"))``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _split_csv(value: str) -> list[str]:
    """Split a comma/semicolon separated setting into a clean list."""
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


class WebSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    title: str = "KYC Grabber"


class ImapSettings(BaseModel):
    """Production mailbox settings. Placeholders until credentials are wired up."""

    host: str = "imap.example.com"
    port: int = 993
    use_ssl: bool = True
    username: str = "kyc-bot@example.com"
    password: str = "change-me"  # noqa: S105 - placeholder, real value comes from .env
    mailbox: str = "INBOX"
    processed_mailbox: str = "Processed"
    mark_seen: bool = True
    idle_timeout_seconds: int = 240
    reconnect_backoff_seconds: int = 15


class MailSettings(BaseModel):
    mode: Literal["filesystem", "imap"] = "filesystem"
    inbox_dir: Path = Path("data/inbox")
    processed_dir: Path = Path("data/inbox/processed")
    poll_interval_seconds: float = 5.0
    include_already_seen: bool = False
    imap: ImapSettings = Field(default_factory=ImapSettings)


class FilterSettings(BaseModel):
    """Decides which inbound emails we react to."""

    allowed_senders: str = "*"
    subject_token: str = "[KYC]"
    allowed_attachment_extensions: str = ".csv"
    max_attachment_bytes: int = 5 * 1024 * 1024
    max_codes_per_request: int = 500
    reply_on_reject: bool = True

    @property
    def sender_patterns(self) -> list[str]:
        return _split_csv(self.allowed_senders)

    @property
    def attachment_extensions(self) -> list[str]:
        return [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in _split_csv(self.allowed_attachment_extensions)]


class SmtpSettings(BaseModel):
    host: str = "smtp.example.com"
    port: int = 587
    use_starttls: bool = True
    username: str = "kyc-bot@example.com"
    password: str = "change-me"  # noqa: S105 - placeholder
    use_ssl: bool = False


class OutboundSettings(BaseModel):
    mode: Literal["filesystem", "smtp"] = "filesystem"
    outbox_dir: Path = Path("data/outbox")
    from_address: str = "kyc-bot@example.com"
    subject_template: str = "KYC report for {filename} ({count} third parties)"
    smtp: SmtpSettings = Field(default_factory=SmtpSettings)


class InternalSettings(BaseModel):
    latency_ms: int = 25
    seed_path: Path = Path("data/internal_db_seed.json")


class ExternalSettings(BaseModel):
    """Knobs for the simulated external registry client."""

    base_url: str = "https://simulated-registry.example.com"  # TODO(integration): real provider URL
    api_key: str = "placeholder"  # noqa: S105 - placeholder
    latency_ms: int = 180
    jitter_ms: int = 120
    failure_rate: float = 0.03
    not_found_rate: float = 0.12
    max_retries: int = 3
    retry_backoff_seconds: float = 0.4
    concurrency: int = 8


class PipelineSettings(BaseModel):
    workers: int = 2
    code_concurrency: int = 6
    code_timeout_seconds: float = 30.0


class ExcelSettings(BaseModel):
    output_dir: Path = Path("data/out")
    include_summary_sheet: bool = True
    include_raw_payload: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KYC_",
        env_nested_delimiter="__",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    data_dir: Path = Path("data")
    store_path: Path = Path("data/kyc_grabber.sqlite3")

    web: WebSettings = Field(default_factory=WebSettings)
    mail: MailSettings = Field(default_factory=MailSettings)
    filter: FilterSettings = Field(default_factory=FilterSettings)
    outbound: OutboundSettings = Field(default_factory=OutboundSettings)
    internal: InternalSettings = Field(default_factory=InternalSettings)
    external: ExternalSettings = Field(default_factory=ExternalSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    excel: ExcelSettings = Field(default_factory=ExcelSettings)

    # ---------------------------------------------------------------- helpers
    def resolve(self, path: Path) -> Path:
        """Resolve a possibly-relative path against the project root."""
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    def absolutize_paths(self) -> None:
        """Rewrite every path setting to an absolute path.

        Components must agree on where ``data/inbox`` lives no matter what the
        process working directory is, so this runs before anything is built.
        Idempotent.
        """
        self.data_dir = self.resolve(self.data_dir)
        self.store_path = self.resolve(self.store_path)
        self.mail.inbox_dir = self.resolve(self.mail.inbox_dir)
        self.mail.processed_dir = self.resolve(self.mail.processed_dir)
        self.outbound.outbox_dir = self.resolve(self.outbound.outbox_dir)
        self.excel.output_dir = self.resolve(self.excel.output_dir)
        self.internal.seed_path = self.resolve(self.internal.seed_path)

    @property
    def data_path(self) -> Path:
        return self.resolve(self.data_dir)

    @property
    def store_file(self) -> Path:
        return self.resolve(self.store_path)

    @property
    def inbox_path(self) -> Path:
        return self.resolve(self.mail.inbox_dir)

    @property
    def processed_path(self) -> Path:
        return self.resolve(self.mail.processed_dir)

    @property
    def outbox_path(self) -> Path:
        return self.resolve(self.outbound.outbox_dir)

    @property
    def output_path(self) -> Path:
        return self.resolve(self.excel.output_dir)

    @property
    def internal_seed_file(self) -> Path:
        return self.resolve(self.internal.seed_path)

    def ensure_directories(self) -> None:
        self.absolutize_paths()
        for path in (
            self.data_path,
            self.inbox_path,
            self.processed_path,
            self.outbox_path,
            self.output_path,
            self.store_file.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict:
        """Config snapshot safe to hand to the dashboard."""
        return {
            "app_env": self.app_env,
            "mail_mode": self.mail.mode,
            "inbox_dir": str(self.inbox_path),
            "outbox_dir": str(self.outbox_path),
            "output_dir": str(self.output_path),
            "store_path": str(self.store_file),
            "imap_host": self.mail.imap.host,
            "imap_mailbox": self.mail.imap.mailbox,
            "from_address": self.outbound.from_address,
            "outbound_mode": self.outbound.mode,
            "subject_token": self.filter.subject_token,
            "allowed_senders": self.filter.sender_patterns,
            "allowed_attachments": self.filter.attachment_extensions,
            "max_codes_per_request": self.filter.max_codes_per_request,
            "external_base_url": self.external.base_url,
            "workers": self.pipeline.workers,
            "code_concurrency": self.pipeline.code_concurrency,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()

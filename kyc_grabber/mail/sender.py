"""Outbound delivery of the KYC workbook.

Two modes are supported:

* ``filesystem`` - the reply is written as a ``.eml`` file into ``data/outbox``.
  This is the default so the whole service can be exercised without SMTP.
* ``smtp`` - a real send, still to be pointed at the corporate relay
  (TODO(integration): fill in credentials and TLS policy).
"""

from __future__ import annotations

import asyncio
import smtplib
from pathlib import Path

from ..config import OutboundSettings
from ..logging_setup import get_logger
from ..models import DeliveryInfo
from .composer import compose_message

logger = get_logger(__name__)


class OutboundMailer:
    def __init__(self, settings: OutboundSettings, outbox_dir: Path) -> None:
        self.settings = settings
        self.outbox_dir = Path(outbox_dir)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    async def send_reply(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        attachments: list[tuple[str, bytes]] | None = None,
        job_id: str = "",
    ) -> DeliveryInfo:
        message = compose_message(
            sender=self.settings.from_address,
            to=to,
            subject=subject,
            body_text=body_text,
            attachments=attachments,
        )
        if self.settings.mode == "smtp":
            await asyncio.to_thread(self._send_smtp, message)
            logger.info("Delivered job %s to %s via SMTP", job_id or "-", to)
            return DeliveryInfo(mode="smtp", target=to, path=None)

        path = self.outbox_dir / f"reply-{job_id or 'manual'}.eml"
        await asyncio.to_thread(path.write_bytes, bytes(message))
        logger.info("Delivered job %s to %s (filesystem outbox: %s)", job_id or "-", to, path)
        return DeliveryInfo(mode="filesystem", target=to, path=str(path))

    # ------------------------------------------------------------------ smtp
    def _send_smtp(self, message) -> None:
        smtp_settings = self.settings.smtp
        if smtp_settings.use_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(smtp_settings.host, smtp_settings.port, timeout=30)
        else:
            client = smtplib.SMTP(smtp_settings.host, smtp_settings.port, timeout=30)
        try:
            client.ehlo()
            if not smtp_settings.use_ssl and smtp_settings.use_starttls:
                client.starttls()
                client.ehlo()
            if smtp_settings.username:
                client.login(smtp_settings.username, smtp_settings.password)
            client.send_message(message)
        finally:
            try:
                client.quit()
            except smtplib.SMTPException:  # pragma: no cover - best effort
                client.close()

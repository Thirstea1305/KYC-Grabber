"""Filesystem mail source - the demo/development stand-in for a real mailbox.

Any ``*.eml`` file dropped into ``data/inbox`` is treated exactly like a message
arriving over IMAP: it is parsed with the same RFC822 parser and pushed through
the same handler. Files are moved to ``data/inbox/processed`` after being read so
the watcher never processes them twice.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from ..logging_setup import get_logger
from ..mail.composer import parse_rfc822
from .base import BaseMailSource, MailHandler

logger = get_logger(__name__)

# Ignore files that were written in the last moments - they may still be growing.
STABILITY_SECONDS = 0.5


class FilesystemMailSource(BaseMailSource):
    name = "filesystem"

    def __init__(self, source_settings, handler: MailHandler) -> None:
        super().__init__(source_settings, handler)
        self.inbox = Path(source_settings.inbox_dir)
        self.processed = Path(source_settings.processed_dir)

    async def _run(self) -> None:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)
        logger.info("Watching %s for *.eml drop-ins (every %.1fs)",
                    self.inbox, self.settings.poll_interval_seconds)
        while self.running:
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the watcher alive
                self.last_error = str(exc)
                logger.exception("Filesystem mailbox scan failed")
            self.last_poll_at = datetime.now(timezone.utc)
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _scan_once(self) -> None:
        for path in sorted(self.inbox.glob("*.eml")):
            if not path.is_file():
                continue
            try:
                if time.time() - path.stat().st_mtime < STABILITY_SECONDS:
                    continue
            except OSError:
                continue

            raw = await asyncio.to_thread(path.read_bytes)
            try:
                mail = parse_rfc822(raw, source=self.name, raw_path=str(path))
            except Exception as exc:  # noqa: BLE001 - a corrupt drop must not stall the watcher
                logger.error("Could not parse %s: %s", path.name, exc)
                self.last_error = f"parse error on {path.name}"
                await self._archive(path)
                continue

            logger.info("Inbound mail detected: from=%s subject=%r attachments=%d",
                        mail.sender, mail.subject, len(mail.attachments))
            await self.submit(mail)
            await self._archive(path)

    async def _archive(self, path: Path) -> None:
        self.processed.mkdir(parents=True, exist_ok=True)
        target = self.processed / path.name
        if target.exists():
            target = self.processed / f"{path.stem}-{int(time.time())}{path.suffix}"
        try:
            await asyncio.to_thread(path.replace, target)
        except OSError as exc:  # pragma: no cover - e.g. file locked on Windows
            logger.warning("Could not archive %s: %s", path, exc)

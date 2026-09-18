"""IMAP mail source - the production intake for the always-on service.

Runs the blocking ``imaplib`` client in a worker thread and pushes parsed mail
into the async queue. It uses IMAP IDLE when the server supports it (instant
notification) and transparently falls back to polling otherwise. Any network
error drops the connection and the outer loop reconnects with a backoff, so the
service survives VPN blips and server restarts.

TODO(integration): point KYC_MAIL__IMAP__* at the real mailbox and, if the
corporate policy requires it, replace the password setting with OAuth2
(XOAUTH2 authenticate call).
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import socket
import threading
from datetime import datetime, timezone

from ..logging_setup import get_logger
from ..mail.composer import parse_rfc822
from .base import BaseMailSource, MailHandler

logger = get_logger(__name__)


class IdleNotSupported(Exception):
    """Raised when the server or client cannot enter IDLE."""


class ImapMailSource(BaseMailSource):
    name = "imap"

    def __init__(self, source_settings, handler: MailHandler) -> None:
        super().__init__(source_settings, handler)
        self._stop_event = threading.Event()

    # ------------------------------------------------------------- async side
    async def _run(self) -> None:
        backoff = max(1, self.settings.imap.reconnect_backoff_seconds)
        while self.running:
            try:
                await asyncio.to_thread(self._session)
            except asyncio.CancelledError:
                self._stop_event.set()
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.error("IMAP session ended: %s - reconnecting in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
            else:
                backoff = max(1, self.settings.imap.reconnect_backoff_seconds)

    async def stop(self) -> None:
        self._stop_event.set()
        await super().stop()

    # ---------------------------------------------------------- blocking side
    def _connect(self) -> imaplib.IMAP4:
        cfg = self.settings.imap
        if cfg.use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(cfg.host, cfg.port, timeout=60)
        else:
            client = imaplib.IMAP4(cfg.host, cfg.port, timeout=60)
        client.login(cfg.username, cfg.password)
        logger.info("IMAP connected: %s@%s/%s", cfg.username, cfg.host, cfg.mailbox)
        return client

    def _session(self) -> None:
        cfg = self.settings.imap
        client = self._connect()
        try:
            status, _ = client.select(cfg.mailbox)
            if status != "OK":
                raise RuntimeError(f"Cannot select mailbox {cfg.mailbox!r}")

            while self.running and not self._stop_event.is_set():
                self._drain(client)
                self.last_poll_at = datetime.now(timezone.utc)
                try:
                    self._wait_for_activity(client)
                except IdleNotSupported:
                    logger.info("IDLE not supported - falling back to polling every %.1fs",
                                self.settings.poll_interval_seconds)
                    self._stop_event.wait(self.settings.poll_interval_seconds)
                with contextlib.suppress(imaplib.IMAP4.error):
                    client.noop()  # keep-alive + surface dropped connections early
        finally:
            with contextlib.suppress(Exception):
                client.logout()
            logger.info("IMAP session closed")

    def _search_criteria(self) -> list[str]:
        return [] if self.settings.include_already_seen else ["UNSEEN"]

    def _drain(self, client: imaplib.IMAP4) -> int:
        """Fetch and queue every message currently matching the search criteria."""
        status, data = client.search(None, *self._search_criteria())
        if status != "OK":
            raise RuntimeError(f"IMAP SEARCH failed: {status}")

        ids = (data[0] or b"").split()
        for num in ids:
            if not self.running or self._stop_event.is_set():
                return 0
            try:
                status, payload = client.fetch(num, "(RFC822)")
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    logger.warning("IMAP FETCH returned no body for message %s", num)
                    continue
                raw = payload[0][1]
                mail = parse_rfc822(raw, source=self.name)
                logger.info("Inbound mail detected: from=%s subject=%r", mail.sender, mail.subject)
                self._enqueue(mail)
            except Exception:  # noqa: BLE001 - skip the bad message, keep the session
                logger.exception("Failed to read IMAP message %s", num)
            finally:
                self._post_process(client, num)
        return len(ids)

    def _enqueue(self, mail) -> None:
        """Hand a mail from the blocking IMAP thread to the async consumer."""
        loop = self._loop
        if loop is None:  # pragma: no cover - defensive
            raise RuntimeError("IMAP source started without an event loop")
        future = asyncio.run_coroutine_threadsafe(self.submit(mail), loop)
        future.result(timeout=30)

    def _post_process(self, client: imaplib.IMAP4, num: bytes) -> None:
        cfg = self.settings.imap
        if cfg.processed_mailbox:
            with contextlib.suppress(imaplib.IMAP4.error):
                client.copy(num, cfg.processed_mailbox)
        if cfg.mark_seen:
            with contextlib.suppress(imaplib.IMAP4.error):
                client.store(num, "+FLAGS", "\\Seen")

    def _wait_for_activity(self, client: imaplib.IMAP4) -> None:
        """Block until the mailbox changes (IDLE) or the idle timeout expires."""
        timeout = max(30, self.settings.imap.idle_timeout_seconds)
        try:
            tag = client._new_tag()  # noqa: SLF001 - imaplib has no public IDLE API
            client.send(tag + b" IDLE\r\n")
            response = client.readline()
        except (imaplib.IMAP4.error, OSError) as exc:
            raise IdleNotSupported(str(exc)) from exc

        if not response.startswith(b"+"):
            raise IdleNotSupported(f"unexpected IDLE continuation: {response!r}")

        try:
            client.socket().settimeout(timeout)
            while self.running and not self._stop_event.is_set():
                line = client.readline()
                if not line:
                    raise OSError("IMAP connection closed while idling")
                if b"EXISTS" in line.upper() or b"RECENT" in line.upper():
                    logger.debug("IDLE wake-up: %r", line)
                    break
        except (TimeoutError, socket.timeout):
            pass  # normal: re-issue IDLE on the next loop
        finally:
            with contextlib.suppress(OSError):
                client.socket().settimeout(60)
            client.send(b"DONE\r\n")
            with contextlib.suppress(imaplib.IMAP4.error, TimeoutError, socket.timeout):
                while True:
                    line = client.readline()
                    if not line or line.startswith(tag):
                        break

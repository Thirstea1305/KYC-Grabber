"""Decides whether an inbound email is a KYC request we should act on."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from ..config import FilterSettings
from ..models import Attachment, RawMail


@dataclass(slots=True)
class FilterDecision:
    accepted: bool
    reason: str = ""
    csv_attachments: list[Attachment] = field(default_factory=list)


class MailFilter:
    """Sender allow-list + subject token + attachment policy.

    Sender patterns (comma separated in configuration):

    ``*``                      accept everybody (dev default)
    ``kyc@corp.com``           exact address
    ``@corp.com``              any address in the domain
    ``*@corp.com``             glob match
    ``re:^analyst-\\d+@corp``  regular expression
    """

    def __init__(self, settings: FilterSettings) -> None:
        self.settings = settings
        self.patterns = settings.sender_patterns or ["*"]
        self.token = settings.subject_token.strip().lower()
        self.extensions = settings.attachment_extensions

    # ------------------------------------------------------------------ sender
    def _sender_allowed(self, sender: str) -> bool:
        sender = (sender or "").lower()
        if not sender:
            return False
        for pattern in self.patterns:
            raw = pattern.strip().lower()
            if raw in {"*", ""}:
                return True
            if raw.startswith("re:"):
                try:
                    if re.search(raw[3:], sender):
                        return True
                except re.error:
                    continue
            elif raw.startswith("@"):
                if sender.endswith(raw):
                    return True
            elif fnmatch.fnmatch(sender, raw):
                return True
        return False

    # ------------------------------------------------------------- attachments
    def _matching_attachments(self, attachments: list[Attachment]) -> tuple[list[Attachment], str]:
        if not self.extensions:
            return list(attachments), ""
        matched: list[Attachment] = []
        rejected: list[str] = []
        for attachment in attachments:
            name = (attachment.filename or "").lower()
            if any(name.endswith(extension) for extension in self.extensions):
                matched.append(attachment)
            else:
                rejected.append(attachment.filename or "<unnamed>")
        if matched:
            return matched, ""
        if rejected:
            return [], f"No supported attachment (expected {', '.join(self.extensions)}); found: {', '.join(rejected)}."
        return [], "The email has no attachments."

    # ---------------------------------------------------------------- evaluate
    def evaluate(self, mail: RawMail) -> FilterDecision:
        if not self._sender_allowed(mail.sender):
            return FilterDecision(False, f"Sender '{mail.sender}' is not on the allow-list.")

        if self.token and self.token not in (mail.subject or "").lower():
            return FilterDecision(False, f"Subject does not contain the token '{self.settings.subject_token}'.")

        candidates, reason = self._matching_attachments(mail.attachments)
        if not candidates:
            return FilterDecision(False, reason)

        accepted: list[Attachment] = []
        for attachment in candidates:
            if len(attachment.content) > self.settings.max_attachment_bytes:
                reason = (f"Attachment '{attachment.filename}' is larger than "
                          f"{self.settings.max_attachment_bytes} bytes.")
                continue
            if not attachment.content.strip():
                reason = f"Attachment '{attachment.filename}' is empty."
                continue
            accepted.append(attachment)

        if not accepted:
            return FilterDecision(False, reason or "No usable attachment found.")

        return FilterDecision(True, "accepted", accepted)

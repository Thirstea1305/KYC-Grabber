"""RFC822 parsing and composition shared by the IMAP and filesystem sources."""

from __future__ import annotations

import hashlib
import html
import re
from email import message_from_bytes
from email.message import EmailMessage, Message
from email.policy import default as default_policy
from email.utils import parseaddr

from ..models import Attachment, ItemResult, RawMail, utcnow

TAG_RE = re.compile(r"<[^>]+>")


def _message_id(message: Message, raw: bytes) -> str:
    value = (message.get("Message-ID") or "").strip()
    if value:
        return value.strip("<>")
    return "synthetic-" + hashlib.sha256(raw).hexdigest()[:24]


def _body_text(message: Message) -> str:
    """Best-effort plain-text body: prefer text/plain, fall back to stripped HTML."""
    if not message.is_multipart():
        payload = message.get_payload(decode=True) or b""
        charset = message.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        if message.get_content_type() == "text/html":
            return html.unescape(TAG_RE.sub(" ", text))
        return text

    plain, rich = "", ""
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        if part.get_content_type() == "text/plain" and not plain:
            plain = text
        elif part.get_content_type() == "text/html" and not rich:
            rich = html.unescape(TAG_RE.sub(" ", text))
    return (plain or rich).strip()


def _attachments(message: Message) -> list[Attachment]:
    found: list[Attachment] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        if (part.get_content_disposition() or "").lower() == "inline":
            continue
        payload = part.get_payload(decode=True) or b""
        found.append(Attachment(filename=filename, content_type=part.get_content_type(), content=payload))
    return found


def parse_rfc822(raw: bytes, *, source: str = "filesystem", raw_path: str | None = None) -> RawMail:
    """Turn raw RFC822 bytes into a normalised :class:`RawMail`."""
    message = message_from_bytes(raw, policy=default_policy)
    sender = parseaddr(message.get("From", ""))[1] or (message.get("From", "") or "").strip()
    return RawMail(
        source=source,
        message_id=_message_id(message, raw),
        sender=sender.lower(),
        subject=(message.get("Subject") or "").strip(),
        received_at=utcnow(),
        body_text=_body_text(message),
        attachments=_attachments(message),
        raw_path=raw_path,
    )


def _mime_for(filename: str) -> tuple[str, str]:
    lowered = filename.lower()
    if lowered.endswith(".csv"):
        return "text", "csv"
    if lowered.endswith(".xlsx"):
        return "application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if lowered.endswith((".txt", ".log")):
        return "text", "plain"
    return "application", "octet-stream"


def compose_message(
    *,
    sender: str,
    to: str,
    subject: str,
    body_text: str,
    attachments: list[tuple[str, bytes]] | None = None,
    message_id: str | None = None,
) -> EmailMessage:
    """Build an RFC822 message (used to simulate inbound mail and to send replies)."""
    message = EmailMessage(policy=default_policy)
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    if message_id:
        message["Message-ID"] = message_id if message_id.startswith("<") else f"<{message_id}>"
    message["Date"] = utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    message.set_content(body_text)
    for filename, content in attachments or []:
        maintype, subtype = _mime_for(filename)
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return message


def render_result_body(
    *,
    job_id: str,
    items: list[ItemResult],
    filename: str,
    warnings: list[str],
) -> str:
    """Human readable summary that accompanies the workbook."""
    lines = [
        f"KYC Grabber - job {job_id}",
        "",
        f"Third parties processed : {len(items)}",
        f"Attachment              : {filename}",
        "",
        "Per third party:",
    ]
    for item in items:
        status = (item.match_status.value if item.match_status else "error")
        name = (item.external or item.internal)
        label = name.legal_name if name else "no record found"
        lines.append(f"  - {item.code:<20} {status:<14} risk={item.risk_rating.value:<8} {label}")
        for difference in item.differences:
            lines.append(f"      ! {difference}")
        if item.error:
            lines.append(f"      ! error: {item.error}")

    if warnings:
        lines += ["", "Warnings:"]
        lines += [f"  - {warning}" for warning in warnings]

    lines += [
        "",
        "The full detail, one worksheet per third party, is in the attached workbook.",
        "Note: records labelled 'simulated' are synthetic demo data.",
        "",
        "-- KYC Grabber",
    ]
    return "\n".join(lines)


def render_rejection_body(*, reason: str, subject: str) -> str:
    return "\n".join([
        "KYC Grabber could not process your request.",
        "",
        f"Subject : {subject}",
        f"Reason  : {reason}",
        "",
        "Please send a CSV attachment containing a 'third_party_code' column.",
        "",
        "-- KYC Grabber",
    ])

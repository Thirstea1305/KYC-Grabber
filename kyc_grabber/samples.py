"""Sample payloads used by the demo flow, the dashboard and the tests."""

from __future__ import annotations

SAMPLE_CSV = """third_party_code,notes
TP-0001,existing low risk vendor
TP-0002,onboarded last quarter
TP-0003,flagged for review
TP-0007,not in the internal master data
EXT-4242,new counterparty
XX-9999,unknown everywhere
"""

SAMPLE_SUBJECT = "[KYC] quarterly third-party screening"
SAMPLE_SENDER = "analyst@example.com"
SAMPLE_FILENAME = "third_parties.csv"
SAMPLE_BODY = (
    "Hi KYC team,\n\n"
    "Please run the standard checks on the third parties in the attached list\n"
    "and send back the usual workbook.\n\n"
    "Thanks,\nCompliance Operations\n"
)


def build_sample_eml(
    *,
    sender: str = SAMPLE_SENDER,
    subject: str = SAMPLE_SUBJECT,
    filename: str = SAMPLE_FILENAME,
    csv_text: str = SAMPLE_CSV,
    body: str = SAMPLE_BODY,
    message_id: str | None = None,
) -> bytes:
    """Render a complete RFC822 message (with CSV attachment) as bytes."""
    from .mail.composer import compose_message

    message = compose_message(
        sender=sender,
        to="kyc-bot@example.com",
        subject=subject,
        body_text=body,
        attachments=[(filename, csv_text.encode("utf-8"))],
        message_id=message_id,
    )
    return bytes(message)

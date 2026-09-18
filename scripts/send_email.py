"""Drop a KYC request email into the watcher inbox.

Handy for testing filter behaviour without a real mail client:

    python scripts/send_email.py                                     # sample payload
    python scripts/send_email.py --csv-file .\\my_codes.csv
    python scripts/send_email.py --subject "no token here"           # expect a rejection
    python scripts/send_email.py --no-attachment                     # expect a rejection
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kyc_grabber.config import get_settings  # noqa: E402
from kyc_grabber.logging_setup import setup_logging  # noqa: E402
from kyc_grabber.samples import SAMPLE_BODY, SAMPLE_CSV, SAMPLE_FILENAME, SAMPLE_SENDER, SAMPLE_SUBJECT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate an inbound KYC request email")
    parser.add_argument("--sender", default=SAMPLE_SENDER)
    parser.add_argument("--subject", default=SAMPLE_SUBJECT)
    parser.add_argument("--filename", default=SAMPLE_FILENAME)
    parser.add_argument("--csv-file", type=Path, default=None, help="CSV to attach (defaults to the built-in sample)")
    parser.add_argument("--no-attachment", action="store_true", help="send without a CSV attachment")
    parser.add_argument("--name", default=None, help="file name for the .eml in the inbox")
    parser.add_argument("--inbox", type=Path, default=None, help="override the inbox directory")
    args = parser.parse_args()

    setup_logging("WARNING")
    settings = get_settings()
    settings.ensure_directories()

    from kyc_grabber.samples import build_sample_eml

    csv_text = SAMPLE_CSV
    if args.csv_file:
        csv_text = args.csv_file.read_text(encoding="utf-8-sig")

    if args.no_attachment:
        from kyc_grabber.mail.composer import compose_message

        raw = bytes(compose_message(
            sender=args.sender, to=settings.outbound.from_address,
            subject=args.subject, body_text=SAMPLE_BODY, attachments=[],
        ))
    else:
        raw = build_sample_eml(
            sender=args.sender, subject=args.subject, filename=args.filename,
            csv_text=csv_text, body=SAMPLE_BODY,
        )

    inbox = args.inbox or settings.inbox_path
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / (args.name or "cli-request.eml")
    target.write_bytes(raw)

    print(f"Wrote {len(raw)} bytes to {target}")
    print(f"  from    : {args.sender}")
    print(f"  subject : {args.subject}")
    if not args.no_attachment:
        print(f"  attach  : {args.filename} ({len(csv_text.splitlines()) - 1} data rows)")
    print("The watcher will pick it up on its next poll (or call POST /api/jobs/... after processing).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

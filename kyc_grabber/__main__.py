"""Command line entry point.

    python -m kyc_grabber run                 # always-on service (watcher + worker + dashboard)
    python -m kyc_grabber run --no-web        # headless: watcher + worker only
    python -m kyc_grabber web                 # dashboard + worker, no mail watcher
    python -m kyc_grabber process <file.eml>  # one-off, processes a mail file and exits
    python -m kyc_grabber sample              # drop a sample KYC email into the inbox
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import get_settings
from .logging_setup import get_logger, setup_logging
from .mail.composer import parse_rfc822
from .runtime import AppContext, build_context
from .samples import build_sample_eml

logger = get_logger("kyc_grabber.cli")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kyc-grabber", description="Email-driven KYC enrichment service")
    parser.add_argument("--log-level", default=None, help="override KYC_LOG_LEVEL")
    sub = parser.add_subparsers(dest="command", required=False)

    run = sub.add_parser("run", help="start the always-on service")
    run.add_argument("--no-web", action="store_true", help="do not serve the dashboard")
    run.add_argument("--no-watcher", action="store_true", help="do not watch the mailbox")
    run.add_argument("--source", choices=["filesystem", "imap"], default=None, help="override the mail source")

    web = sub.add_parser("web", help="dashboard and workers only")
    web.add_argument("--source", choices=["filesystem", "imap"], default=None)

    process = sub.add_parser("process", help="process a single .eml file and exit")
    process.add_argument("path", type=Path)

    sample = sub.add_parser("sample", help="write a sample KYC request into the mailbox")
    sample.add_argument("--sender", default=None)
    sample.add_argument("--subject", default=None)
    sample.add_argument("--filename", default=None)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    if args.log_level:
        settings.log_level = args.log_level
    setup_logging(settings.log_level)

    command = args.command or "run"
    if command == "run":
        from .supervisor import run

        context = build_context(settings, source_mode=args.source)
        run(context, with_web=not args.no_web, with_source=not args.no_watcher)
        return 0

    if command == "web":
        from .supervisor import run

        context = build_context(settings, source_mode=args.source)
        run(context, with_web=True, with_source=False)
        return 0

    if command == "process":
        context = build_context(settings, source_mode="filesystem")
        return asyncio.run(_process_file(context, args.path))

    if command == "sample":
        return _write_sample(settings, args)

    return 1  # pragma: no cover


async def _process_file(context: AppContext, path: Path) -> int:
    if not path.is_file():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2

    mail = parse_rfc822(path.read_bytes(), source="cli", raw_path=str(path))
    events: list[str] = []

    original = context.bus.publish

    def capture(event: dict) -> None:
        events.append(f"  {event['level']:<5} {event['message']}")
        original(event)

    context.bus.publish = capture  # type: ignore[method-assign]
    try:
        job_id = await context.pipeline.ingest(mail)
        if job_id is None:
            print("Email was rejected or already processed:")
            print("\n".join(events) or "  (no events)")
            return 0
        await context.pipeline.process_job(job_id)
    finally:
        context.bus.publish = original  # type: ignore[method-assign]

    print("\n".join(events))
    job = context.store.get_job(job_id)
    print(f"\njob      : {job_id}\nstatus   : {job['status']}")
    print(f"workbook : {job['workbook_path']}")
    print(f"delivered: {job['delivered_to']} ({job['delivery_mode']})")
    context.close()
    return 0 if job["status"] == "completed" else 1


def _write_sample(settings, args: argparse.Namespace) -> int:
    from .samples import SAMPLE_FILENAME, SAMPLE_SENDER, SAMPLE_SUBJECT

    settings.ensure_directories()
    target = settings.inbox_path / "sample-kyc-request.eml"
    target.write_bytes(
        build_sample_eml(
            sender=args.sender or SAMPLE_SENDER,
            subject=args.subject or SAMPLE_SUBJECT,
            filename=args.filename or SAMPLE_FILENAME,
        )
    )
    print(f"Sample KYC email written to {target}")
    print("Start the service with 'python -m kyc_grabber run' to see it processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

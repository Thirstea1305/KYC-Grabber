"""Logging configuration for the service."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    stream = sys.stdout
    try:  # Windows consoles default to cp1252 - avoid UnicodeEncodeError on log glyphs.
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=stream,
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

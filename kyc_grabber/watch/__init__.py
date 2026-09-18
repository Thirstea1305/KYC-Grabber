"""Mail intake sources (filesystem simulation and real IMAP)."""

from .base import BaseMailSource, MailHandler
from .filesystem_source import FilesystemMailSource
from .filter import FilterDecision, MailFilter
from .imap_source import ImapMailSource

__all__ = [
    "BaseMailSource",
    "FilesystemMailSource",
    "FilterDecision",
    "ImapMailSource",
    "MailFilter",
    "MailHandler",
    "build_source",
]


def build_source(source_settings, handler: MailHandler) -> BaseMailSource:
    """Factory: pick the intake implementation from configuration."""
    if source_settings.mode == "imap":
        return ImapMailSource(source_settings, handler)
    return FilesystemMailSource(source_settings, handler)

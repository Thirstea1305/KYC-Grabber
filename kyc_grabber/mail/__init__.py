"""Email intake and delivery adapters."""

from .composer import compose_message, parse_rfc822, render_result_body
from .sender import OutboundMailer

__all__ = ["OutboundMailer", "compose_message", "parse_rfc822", "render_result_body"]

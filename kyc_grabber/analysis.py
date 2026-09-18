"""Reconciliation logic: compare internal vs external records and score risk."""

from __future__ import annotations

import re
import unicodedata

from .models import (
    ExternalRecord,
    InternalRecord,
    MatchStatus,
    RiskRating,
    max_risk,
)

LEGAL_SUFFIXES = {
    "ltd", "limited", "llc", "llp", "lp", "inc", "incorporated", "corp", "corporation",
    "company", "co", "gmbh", "ag", "bv", "nv", "sa", "sas", "sarl", "srl", "spa",
    "plc", "pte", "pty", "oy", "ab", "as", "aps", "kk", "kg", "ug", "sl", "sro",
    "group", "holdings", "holding", "international", "global", "the",
}


def normalize_name(value: str | None) -> str:
    """Lowercase, strip accents/punctuation and drop common legal suffixes."""
    if not value:
        return ""
    # Collapse dotted abbreviations first so "S.L." becomes the "sl" suffix
    # that can then be recognised and dropped.
    collapsed = re.sub(r"\b([A-Za-z])\.", r"\1", value)
    decomposed = unicodedata.normalize("NFKD", collapsed)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    tokens = re.sub(r"[^a-z0-9 ]+", " ", ascii_only.lower()).split()
    trimmed = [token for token in tokens if token not in LEGAL_SUFFIXES]
    return " ".join(trimmed or tokens)


def names_match(left: str | None, right: str | None) -> bool:
    a, b = normalize_name(left), normalize_name(right)
    if not a or not b:
        return False
    if a == b:
        return True
    a_tokens, b_tokens = set(a.split()), set(b.split())
    if a_tokens == b_tokens:
        return True
    shorter, longer = sorted((a, b), key=len)
    return bool(shorter) and shorter in longer


def _country(value: str | None) -> str:
    return (value or "").strip().upper()


def compare(
    internal: InternalRecord | None,
    external: ExternalRecord | None,
) -> tuple[MatchStatus, list[str], list[str]]:
    """Return ``(match_status, differences, warnings)`` for one code."""
    differences: list[str] = []
    warnings: list[str] = []

    if internal is None and external is None:
        return MatchStatus.NOT_FOUND, differences, ["Not present in either source."]
    if internal is None:
        warnings.append("Unknown to the internal master data - possible un-onboarded third party.")
        return MatchStatus.EXTERNAL_ONLY, differences, warnings
    if external is None:
        warnings.append("No external registry record found - manual verification required.")
        return MatchStatus.INTERNAL_ONLY, differences, warnings

    if not names_match(internal.legal_name, external.legal_name):
        differences.append(f"Legal name: internal='{internal.legal_name}' vs external='{external.legal_name}'")

    if _country(internal.country) != _country(external.country):
        differences.append(f"Country: internal='{internal.country}' vs external='{external.country}'")

    if normalize_name(internal.entity_type) != normalize_name(external.entity_type):
        differences.append(f"Entity type: internal='{internal.entity_type}' vs external='{external.entity_type}'")

    status = MatchStatus.MISMATCH if differences else MatchStatus.MATCH

    if internal.status and external.status and internal.status.lower() != external.status.lower():
        warnings.append(f"Status differs: internal='{internal.status}' vs external='{external.status}'")

    return status, differences, warnings


def derive_risk(internal: InternalRecord | None, external: ExternalRecord | None) -> RiskRating:
    """Highest of the declared internal rating and any screening red flag."""
    ratings: list[RiskRating] = []
    if internal is not None:
        ratings.append(internal.risk_rating)
        if internal.sanctions_hits:
            ratings.append(RiskRating.CRITICAL)
        if internal.pep_flags:
            ratings.append(RiskRating.HIGH)

    if external is not None:
        if external.sanctions_listed:
            ratings.append(RiskRating.CRITICAL)
        if external.pep_match:
            ratings.append(RiskRating.HIGH)
        if external.adverse_media_count >= 3:
            ratings.append(RiskRating.HIGH)
        elif external.adverse_media_count >= 1:
            ratings.append(RiskRating.MEDIUM)

    return max_risk(*ratings)

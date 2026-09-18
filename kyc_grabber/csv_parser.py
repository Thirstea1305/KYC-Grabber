"""Tolerant CSV parsing: extract third-party codes from an arbitrary export."""

from __future__ import annotations

import csv
import io
import re

from .models import ParseOutcome, ThirdPartyCode

# Header names we recognise (compared after normalisation: lowercase, no spaces/underscores).
CODE_HEADER_CANDIDATES = (
    "thirdpartycode",
    "thirdparty",
    "thirdpartyid",
    "counterpartycode",
    "counterparty",
    "vendorcode",
    "entitycode",
    "clientcode",
    "code",
    "identifier",
    "ref",
    "reference",
    "externalid",
    "id",
)

CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{1,63}$")
DELIMITERS = [",", ";", "\t", "|"]
MAX_REPORTED_ERRORS = 25


def _decode(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8?replace"


def _normalise_header(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


def _pick_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        counts = {delimiter: sample.count(delimiter) for delimiter in DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


def _clean_code(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def parse_codes(raw: bytes, filename: str = "upload.csv") -> ParseOutcome:
    """Parse *raw* CSV bytes into a de-duplicated, validated list of codes."""
    text, _encoding = _decode(raw)
    if not text.strip():
        return ParseOutcome(source_file=filename, errors=["File is empty."])

    delimiter = _pick_delimiter(text[:4096])
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ParseOutcome(source_file=filename, errors=["No data rows found."])

    header_cells = rows[0]
    code_index: int | None = None
    data_rows = rows[1:]

    for index, cell in enumerate(header_cells):
        if _normalise_header(cell) in CODE_HEADER_CANDIDATES:
            code_index = index
            break

    if code_index is None:
        # No recognisable header: assume the first column holds the codes, and
        # treat row 0 as a header only when it does not look like a code.
        code_index = 0
        if not CODE_RE.match(_clean_code(header_cells[0] if header_cells else "")):
            data_rows = rows[1:]
        else:
            data_rows = rows

    outcome = ParseOutcome(source_file=filename)
    seen: set[str] = set()
    error_count = 0

    def record_error(message: str) -> None:
        nonlocal error_count
        error_count += 1
        if len(outcome.errors) < MAX_REPORTED_ERRORS:
            outcome.errors.append(message)

    for offset, row in enumerate(data_rows, start=2 if len(rows) > len(data_rows) else 1):
        if code_index >= len(row):
            continue
        raw_code = _clean_code(row[code_index])
        if not raw_code:
            continue
        code = raw_code.upper()
        if not CODE_RE.match(code):
            record_error(f"Row {offset}: '{raw_code}' is not a valid third-party code - skipped.")
            continue
        if code in seen:
            outcome.duplicates += 1
            continue
        seen.add(code)
        outcome.codes.append(ThirdPartyCode(code=code, row_number=offset, source_file=filename))

    if error_count > len(outcome.errors):
        outcome.errors.append(f"... and {error_count - len(outcome.errors)} more invalid rows.")

    if not outcome.codes and not outcome.errors:
        outcome.errors.append("No third-party codes found in the file.")

    return outcome

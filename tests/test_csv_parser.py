from __future__ import annotations

from kyc_grabber.csv_parser import parse_codes

BOM = b"\xef\xbb\xbf"


def test_detects_code_column_by_header_name() -> None:
    raw = BOM + b"third_party_code,notes\nTP-0001,first\nTP-0002,second\n"
    outcome = parse_codes(raw, "codes.csv")
    assert [c.code for c in outcome.codes] == ["TP-0001", "TP-0002"]
    assert [c.row_number for c in outcome.codes] == [2, 3]
    assert outcome.errors == []


def test_accepts_alternative_header_names_and_variants() -> None:
    raw = b"Vendor Code;Comment\nabc-1;x\n"
    outcome = parse_codes(raw, "codes.csv")
    assert [c.code for c in outcome.codes] == ["ABC-1"]


def test_headerless_single_column_file_uses_first_column() -> None:
    raw = b"TP-0001\nTP-0002\n"
    outcome = parse_codes(raw, "codes.csv")
    assert [c.code for c in outcome.codes] == ["TP-0001", "TP-0002"]


def test_skips_headerless_first_row_when_it_cannot_be_a_code() -> None:
    raw = b"code list\nTP-0001\n"
    outcome = parse_codes(raw, "codes.csv")
    assert [c.code for c in outcome.codes] == ["TP-0001"]


def test_tab_delimited_and_extra_columns() -> None:
    raw = b"id\tname\nTP-0009\tNorthwind\n"
    outcome = parse_codes(raw, "codes.tsv")
    assert [c.code for c in outcome.codes] == ["TP-0009"]


def test_deduplicates_and_counts_duplicates() -> None:
    raw = b"third_party_code\nTP-0001\ntp-0001\nTP-0001\n"
    outcome = parse_codes(raw, "codes.csv")
    assert [c.code for c in outcome.codes] == ["TP-0001"]
    assert outcome.duplicates == 2


def test_reports_invalid_codes_but_keeps_valid_ones() -> None:
    raw = b"third_party_code\nTP-0001\nbad code!!\nX\nTP-0002\n"
    outcome = parse_codes(raw, "codes.csv")
    assert [c.code for c in outcome.codes] == ["TP-0001", "TP-0002"]
    assert len(outcome.errors) == 2
    assert "not a valid third-party code" in outcome.errors[0]


def test_empty_file_reports_error() -> None:
    outcome = parse_codes(b"   \n", "codes.csv")
    assert outcome.codes == []
    assert outcome.errors == ["File is empty."]


def test_header_only_file_reports_error() -> None:
    outcome = parse_codes(b"third_party_code,notes\n", "codes.csv")
    assert outcome.codes == []
    assert outcome.errors == ["No third-party codes found in the file."]


def test_cp1252_fallback() -> None:
    raw = "third_party_code,notes\nTP-0001,caf\xe9\n".encode("cp1252")
    outcome = parse_codes(raw, "codes.csv")
    assert [c.code for c in outcome.codes] == ["TP-0001"]

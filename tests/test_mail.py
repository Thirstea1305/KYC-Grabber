from __future__ import annotations

from kyc_grabber.mail.composer import compose_message, parse_rfc822


def test_round_trip_preserves_sender_subject_and_attachment() -> None:
    raw = bytes(compose_message(
        sender="analyst@corp.com",
        to="kyc-bot@corp.com",
        subject="[KYC] screening",
        body_text="please check",
        attachments=[("codes.csv", b"third_party_code\nTP-0001\n")],
        message_id="<abc@corp.com>",
    ))
    mail = parse_rfc822(raw)

    assert mail.sender == "analyst@corp.com"
    assert mail.subject == "[KYC] screening"
    assert mail.message_id == "abc@corp.com"
    assert mail.body_text.strip() == "please check"
    assert len(mail.attachments) == 1
    assert mail.attachments[0].filename == "codes.csv"
    assert mail.attachments[0].content == b"third_party_code\nTP-0001\n"


def test_message_id_is_synthesised_when_missing() -> None:
    raw = b"From: a@b.com\r\nSubject: no id\r\n\r\nbody\r\n"
    first = parse_rfc822(raw)
    second = parse_rfc822(raw)
    assert first.message_id.startswith("synthetic-")
    assert first.message_id == second.message_id  # stable dedupe key


def test_html_only_body_is_converted_to_text() -> None:
    raw = (b"From: a@b.com\r\nSubject: html\r\nMessage-ID: <h@b.com>\r\n"
           b"Content-Type: text/html; charset=utf-8\r\n\r\n"
           b"<html><body><p>Hello <b>KYC</b></p></body></html>")
    mail = parse_rfc822(raw)
    assert "Hello" in mail.body_text
    assert "<" not in mail.body_text


def test_sender_is_normalised_to_bare_address() -> None:
    raw = b"From: \"Analyst, Ops\" <Analyst@Corp.com>\r\nSubject: s\r\nMessage-ID: <x@y>\r\n\r\nb"
    assert parse_rfc822(raw).sender == "analyst@corp.com"


def test_encoded_attachment_filename_is_decoded() -> None:
    raw = (b"From: a@b.com\r\nSubject: s\r\nMessage-ID: <e@b.com>\r\n"
           b"Content-Type: multipart/mixed; boundary=BOUND\r\n\r\n"
           b"--BOUND\r\nContent-Type: text/plain\r\n\r\nsee file\r\n"
           b"--BOUND\r\nContent-Type: text/csv; name=\"=?utf-8?Q?caf=C3=A9-export.csv?=\"\r\n"
           b"Content-Disposition: attachment; filename=\"=?utf-8?Q?caf=C3=A9-export.csv?=\"\r\n\r\n"
           b"third_party_code\nTP-0001\n\r\n--BOUND--\r\n")
    mail = parse_rfc822(raw)
    assert [a.filename for a in mail.attachments] == ["caf\u00e9-export.csv"]

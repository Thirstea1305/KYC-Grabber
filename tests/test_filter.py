from __future__ import annotations

from kyc_grabber.config import FilterSettings
from kyc_grabber.models import Attachment, RawMail
from kyc_grabber.watch.filter import MailFilter


def make_mail(sender="analyst@corp.com", subject="[KYC] please screen", attachments=None) -> RawMail:
    return RawMail(
        message_id="<m1@corp.com>",
        sender=sender,
        subject=subject,
        body_text="see attached",
        attachments=attachments if attachments is not None else [
            Attachment(filename="codes.csv", content_type="text/csv", content=b"third_party_code\nTP-0001\n")
        ],
    )


def test_accepts_matching_email() -> None:
    decision = MailFilter(FilterSettings(subject_token="[KYC]", allowed_senders="*")).evaluate(make_mail())
    assert decision.accepted
    assert [a.filename for a in decision.csv_attachments] == ["codes.csv"]


def test_rejects_wrong_subject_token() -> None:
    decision = MailFilter(FilterSettings(subject_token="[KYC]")).evaluate(make_mail(subject="hello"))
    assert not decision.accepted
    assert "token" in decision.reason


def test_subject_token_match_is_case_insensitive() -> None:
    decision = MailFilter(FilterSettings(subject_token="[KYC]")).evaluate(make_mail(subject="[kyc] hi"))
    assert decision.accepted


def test_empty_subject_token_accepts_any_subject() -> None:
    decision = MailFilter(FilterSettings(subject_token="")).evaluate(make_mail(subject="anything"))
    assert decision.accepted


def test_sender_allowlist_exact_domain_glob_and_regex() -> None:
    assert MailFilter(FilterSettings(allowed_senders="analyst@corp.com")).evaluate(make_mail()).accepted
    assert not MailFilter(FilterSettings(allowed_senders="other@corp.com")).evaluate(make_mail()).accepted
    assert MailFilter(FilterSettings(allowed_senders="@corp.com")).evaluate(make_mail()).accepted
    assert not MailFilter(FilterSettings(allowed_senders="@other.com")).evaluate(make_mail()).accepted
    assert MailFilter(FilterSettings(allowed_senders="*@corp.com")).evaluate(make_mail()).accepted
    assert MailFilter(FilterSettings(allowed_senders=r"re:^analyst-\d+@|analyst@corp\.com$")).evaluate(make_mail()).accepted


def test_rejects_unsupported_attachment_type() -> None:
    mail = make_mail(attachments=[Attachment(filename="notes.txt", content=b"nope")])
    decision = MailFilter(FilterSettings()).evaluate(mail)
    assert not decision.accepted
    assert "expected .csv" in decision.reason


def test_rejects_when_no_attachment() -> None:
    decision = MailFilter(FilterSettings()).evaluate(make_mail(attachments=[]))
    assert not decision.accepted
    assert "no attachments" in decision.reason


def test_rejects_oversized_attachment() -> None:
    mail = make_mail(attachments=[
        Attachment(filename="big.csv", content=b"third_party_code\nTP-0001\n" + b"x" * 5000)
    ])
    decision = MailFilter(FilterSettings(max_attachment_bytes=1024)).evaluate(mail)
    assert not decision.accepted
    assert "larger than" in decision.reason


def test_rejects_empty_attachment() -> None:
    mail = make_mail(attachments=[Attachment(filename="empty.csv", content=b"   ")])
    decision = MailFilter(FilterSettings()).evaluate(mail)
    assert not decision.accepted
    assert "empty" in decision.reason


def test_picks_only_csv_when_several_attachments() -> None:
    mail = make_mail(attachments=[
        Attachment(filename="image.png", content=b"png"),
        Attachment(filename="codes.csv", content=b"third_party_code\nTP-0002\n"),
    ])
    decision = MailFilter(FilterSettings()).evaluate(mail)
    assert decision.accepted
    assert [a.filename for a in decision.csv_attachments] == ["codes.csv"]

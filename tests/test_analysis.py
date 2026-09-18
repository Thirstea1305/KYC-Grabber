from __future__ import annotations

from kyc_grabber.analysis import compare, derive_risk, names_match, normalize_name
from kyc_grabber.models import ExternalRecord, InternalRecord, MatchStatus, RiskRating
from kyc_grabber.models import max_risk


def test_normalize_name_strips_legal_suffixes_and_punctuation() -> None:
    assert normalize_name("Aurora Logistics Ltd.") == "aurora logistics"
    assert normalize_name("AURORA  LOGISTICS, Limited") == "aurora logistics"
    assert normalize_name("Lumière Partners S.L.") == "lumiere partners"
    assert normalize_name(None) == ""


def test_names_match_tolerates_suffix_and_containment_differences() -> None:
    assert names_match("Aurora Logistics Ltd", "Aurora Logistics Limited")
    assert names_match("Aurora Logistics", "Aurora Logistics Group Holdings")
    assert not names_match("Aurora Logistics", "Meridian Trading")


def test_max_risk_picks_the_highest() -> None:
    assert max_risk(RiskRating.LOW, "high", None) is RiskRating.HIGH
    assert max_risk(None, "nonsense") is RiskRating.UNRATED
    assert max_risk(RiskRating.CRITICAL, RiskRating.LOW) is RiskRating.CRITICAL


def internal(**overrides) -> InternalRecord:
    base = {
        "code": "TP-0001",
        "legal_name": "Aurora Logistics Ltd",
        "entity_type": "Private Limited Company",
        "country": "GB",
        "risk_rating": RiskRating.LOW,
    }
    base.update(overrides)
    return InternalRecord(**base)


def external(**overrides) -> ExternalRecord:
    base = {
        "code": "TP-0001",
        "registry_name": "Companies House (UK)",
        "registration_number": "A1234567",
        "legal_name": "Aurora Logistics Ltd",
        "entity_type": "Private Limited Company",
        "country": "GB",
    }
    base.update(overrides)
    return ExternalRecord(**base)


def test_match_when_all_key_fields_agree() -> None:
    status, differences, warnings = compare(internal(), external())
    assert status is MatchStatus.MATCH
    assert differences == []
    assert warnings == []


def test_mismatch_lists_every_differing_field() -> None:
    status, differences, _ = compare(internal(), external(country="IE", entity_type="GmbH"))
    assert status is MatchStatus.MISMATCH
    assert len(differences) == 2
    assert any("Country" in d for d in differences)
    assert any("Entity type" in d for d in differences)


def test_status_disagreement_is_a_warning_not_a_difference() -> None:
    status, differences, warnings = compare(internal(status="active"), external(status="dissolved"))
    assert status is MatchStatus.MATCH
    assert differences == []
    assert any("Status differs" in w for w in warnings)


def test_internal_only_and_external_only() -> None:
    status, _, warnings = compare(internal(), None)
    assert status is MatchStatus.INTERNAL_ONLY
    assert warnings

    status, _, warnings = compare(None, external())
    assert status is MatchStatus.EXTERNAL_ONLY
    assert warnings


def test_not_found_when_neither_source_has_the_code() -> None:
    status, _, warnings = compare(None, None)
    assert status is MatchStatus.NOT_FOUND
    assert warnings


def test_risk_escalates_on_screening_flags() -> None:
    assert derive_risk(internal(), external()) is RiskRating.LOW
    assert derive_risk(internal(pep_flags=1), external()) is RiskRating.HIGH
    assert derive_risk(internal(), external(sanctions_listed=True, sanctions_lists=["OFAC SDN"])) is RiskRating.CRITICAL
    assert derive_risk(internal(), external(adverse_media_count=1)) is RiskRating.MEDIUM
    assert derive_risk(internal(sanctions_hits=1), external()) is RiskRating.CRITICAL
    assert derive_risk(None, None) is RiskRating.UNRATED

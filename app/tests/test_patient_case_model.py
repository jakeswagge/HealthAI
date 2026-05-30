"""Unit tests for the PatientCase pydantic model and its coercions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.patient_case import Decision, PatientCase


class TestDefaults:
    def test_all_optional_defaults(self):
        case = PatientCase()
        assert case.patient_name is None
        assert case.icd10_codes == []
        assert case.cpt_codes == []
        assert case.decision is Decision.UNKNOWN
        assert case.confidence_score == 0.0


class TestNullCoercion:
    @pytest.mark.parametrize("value", ["", "  ", "null", "N/A", "none", "NA"])
    def test_string_nulls_become_none(self, value):
        case = PatientCase(patient_name=value)
        assert case.patient_name is None

    def test_real_value_preserved_and_trimmed(self):
        case = PatientCase(patient_name="  Jane Doe  ")
        assert case.patient_name == "Jane Doe"


class TestCodeListCoercion:
    def test_single_string_to_list(self):
        case = PatientCase(icd10_codes="M51.16")
        assert case.icd10_codes == ["M51.16"]

    def test_none_to_empty_list(self):
        case = PatientCase(cpt_codes=None)
        assert case.cpt_codes == []

    def test_dedup_and_uppercase(self):
        case = PatientCase(icd10_codes=["m51.16", "M51.16", "k21.9"])
        assert case.icd10_codes == ["M51.16", "K21.9"]

    def test_filters_null_markers(self):
        case = PatientCase(cpt_codes=["73721", "N/A", "", None])
        assert case.cpt_codes == ["73721"]


class TestDecisionCoercion:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Approved", Decision.APPROVED),
            ("FAVORABLE DETERMINATION", Decision.APPROVED),
            ("authorized", Decision.APPROVED),
            ("Denied", Decision.DENIED),
            ("ADVERSE DETERMINATION", Decision.DENIED),
            ("not medically necessary denial", Decision.DENIED),
            ("Partially approved", Decision.PARTIAL),
            ("", Decision.UNKNOWN),
            (None, Decision.UNKNOWN),
            ("something weird", Decision.UNKNOWN),
        ],
    )
    def test_decision_mapping(self, raw, expected):
        case = PatientCase(decision=raw)
        assert case.decision is expected


class TestConfidenceCoercion:
    def test_clamped_high(self):
        assert PatientCase(confidence_score=5).confidence_score == 1.0

    def test_clamped_low(self):
        assert PatientCase(confidence_score=-2).confidence_score == 0.0

    def test_string_number(self):
        assert PatientCase(confidence_score="0.83").confidence_score == 0.83

    def test_invalid_becomes_zero(self):
        assert PatientCase(confidence_score="high").confidence_score == 0.0


class TestDerived:
    def test_completeness_full(self):
        case = PatientCase(
            patient_name="A",
            member_id="B",
            date_of_birth="01/01/2000",
            diagnosis="dx",
            icd10_codes=["X00.0"],
            requested_service="svc",
            cpt_codes=["12345"],
            insurance_company="Payer",
            decision="approved",
            physician_name="Dr X",
        )
        assert case.completeness == 1.0

    def test_completeness_empty(self):
        assert PatientCase().completeness == 0.0

    def test_summary_for_denial(self):
        case = PatientCase(
            patient_name="Jane Doe",
            insurance_company="Acme Health",
            requested_service="MRI",
            decision="denied",
            denial_reason="Not medically necessary",
        )
        s = case.summary()
        assert "Jane Doe" in s
        assert "DENIED" in s
        assert "Not medically necessary" in s


class TestSchemaCompliance:
    def test_rejects_out_of_schema_via_json(self):
        # Extra keys are ignored by default (model is lenient); ensure required
        # typed fields still validate.
        case = PatientCase.model_validate(
            {"patient_name": "X", "confidence_score": 0.5, "extra": "ignored"}
        )
        assert case.patient_name == "X"

    def test_confidence_out_of_range_constructed_directly_is_coerced(self):
        # The before-validator clamps, so this should not raise.
        case = PatientCase(confidence_score=1.5)
        assert case.confidence_score == 1.0

"""Tests for the deterministic AppealLetterBuilder."""

from __future__ import annotations

import pytest

from app.appeals.builder import (
    MAY_BE_REQUIRED,
    NOT_AVAILABLE,
    SECTION_HEADERS,
    AppealLetterBuilder,
)
from app.guidelines.repository import get_default_repository
from app.models.appeal_letter import AppealLetter
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.review.engine import ClinicalReviewEngine


def _review_for(case: PatientCase, document_text: str | None = None) -> ReviewResult:
    return ClinicalReviewEngine().review(case, document_text)


HUMIRA_DENIAL = PatientCase(
    patient_name="Harold T. Greene",
    member_id="WP-558210334",
    date_of_birth="04/17/1971",
    diagnosis="Moderate to severe rheumatoid arthritis",
    icd10_codes=["M06.9"],
    requested_service="Humira (adalimumab)",
    cpt_codes=["J0135"],
    insurance_company="WellPoint National Insurance",
    decision=Decision.DENIED,
    denial_reason="Step therapy requirement not met: no documented trial of a conventional DMARD (methotrexate).",
    physician_name="Dr. Susan A. Patel, MD",
)

APPROVAL_CASE = PatientCase(
    patient_name="Maria Approved",
    diagnosis="Moderate to severe rheumatoid arthritis",
    requested_service="Humira (adalimumab)",
    decision=Decision.APPROVED,
)
APPROVAL_DOC = (
    "Moderate to severe rheumatoid arthritis. Failed methotrexate (DMARD). "
    "Negative TB screen. Rheumatologist prescribing."
)


class TestLetterCompleteness:
    def test_all_sections_present(self):
        review = _review_for(HUMIRA_DENIAL)
        appeal = AppealLetterBuilder().build(HUMIRA_DENIAL, review)
        for header in SECTION_HEADERS:
            assert f"## {header}" in appeal.letter_text, header

    def test_letter_includes_identity(self):
        review = _review_for(HUMIRA_DENIAL)
        appeal = AppealLetterBuilder().build(HUMIRA_DENIAL, review)
        assert "Harold T. Greene" in appeal.letter_text
        assert "WP-558210334" in appeal.letter_text
        assert "Humira (adalimumab)" in appeal.letter_text

    def test_returns_appeal_letter_instance(self):
        review = _review_for(HUMIRA_DENIAL)
        appeal = AppealLetterBuilder().build(HUMIRA_DENIAL, review)
        assert isinstance(appeal, AppealLetter)
        assert appeal.has_letter
        assert appeal.appeal_id.startswith("APL-")


class TestSuccessCriterion:
    """Humira denial -> appeal challenges rationale, cites guideline, flags gaps."""

    def test_humira_denial_appeal(self):
        repo = get_default_repository()
        guideline = repo.get("GL-HUMIRA-001")
        review = _review_for(HUMIRA_DENIAL)
        appeal = AppealLetterBuilder().build(HUMIRA_DENIAL, review, guideline)

        text = appeal.letter_text.lower()
        # Challenges the denial rationale (mentions step therapy / DMARD).
        assert "step therapy" in text or "dmard" in text
        # References guideline support.
        assert any("GL-HUMIRA-001" in s for s in appeal.guideline_support)
        # Identifies missing documentation.
        assert appeal.missing_information
        # Original decision captured.
        assert appeal.original_decision == "denied"


class TestApprovalCase:
    def test_approval_has_no_missing_gaps(self):
        review = _review_for(APPROVAL_CASE, APPROVAL_DOC)
        assert review.recommendation is Recommendation.APPROVE
        appeal = AppealLetterBuilder().build(APPROVAL_CASE, review)
        # Fully supported -> no missing information items.
        assert appeal.missing_information == []
        # Letter still complete.
        for header in SECTION_HEADERS:
            assert f"## {header}" in appeal.letter_text


class TestMissingInformationSafety:
    def test_missing_fields_use_safe_language(self):
        sparse = PatientCase(
            requested_service="Humira (adalimumab)",
            decision=Decision.DENIED,
            denial_reason="Denied.",
        )
        review = _review_for(sparse)
        appeal = AppealLetterBuilder().build(sparse, review)
        # No patient identity -> safe fallback phrasing in the letter.
        assert NOT_AVAILABLE in appeal.letter_text
        # Patient name was absent; must not be fabricated.
        assert appeal.patient_name is None

    def test_missing_evidence_phrase_present_when_gaps(self):
        review = _review_for(HUMIRA_DENIAL)
        appeal = AppealLetterBuilder().build(HUMIRA_DENIAL, review)
        combined = " ".join(appeal.missing_information) + appeal.letter_text
        assert NOT_AVAILABLE in combined or MAY_BE_REQUIRED in combined


class TestNoFabrication:
    def test_absent_diagnosis_not_invented(self):
        case = PatientCase(
            requested_service="Humira (adalimumab)",
            decision=Decision.DENIED,
            denial_reason="Step therapy not met (no DMARD).",
        )
        review = _review_for(case)
        appeal = AppealLetterBuilder().build(case, review)
        # Clinical summary must acknowledge the missing diagnosis, not assert one.
        assert NOT_AVAILABLE in appeal.clinical_summary

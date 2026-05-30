"""Tests for the deterministic ClinicalReviewEngine."""

from __future__ import annotations

from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.review.engine import ClinicalReviewEngine


class TestSuccessCriterion:
    """Humira denied due to missing step therapy (the M3 success criterion)."""

    def test_humira_missing_step_therapy(self):
        engine = ClinicalReviewEngine()
        case = PatientCase(
            diagnosis="Rheumatoid arthritis",
            icd10_codes=["M06.9"],
            requested_service="Humira (adalimumab)",
            cpt_codes=["J0135"],
            decision=Decision.DENIED,
            denial_reason=(
                "Step therapy requirement not met: no documented trial and "
                "failure of methotrexate (a conventional DMARD)."
            ),
        )
        result = engine.review(case)

        assert isinstance(result, ReviewResult)
        assert result.recommendation is Recommendation.DENY
        assert result.guideline_id == "GL-HUMIRA-001"

        # Identifies the unmet guideline requirement (step therapy).
        joined_missing = " ".join(result.missing_criteria).lower()
        assert "dmard" in joined_missing or "step therapy" in joined_missing

        # Identifies missing evidence and provides a rationale.
        assert result.missing_evidence
        assert result.rationale
        assert result.recommended_actions


class TestApprovals:
    def test_full_criteria_approve(self):
        engine = ClinicalReviewEngine()
        case = PatientCase(
            diagnosis="Moderate to severe rheumatoid arthritis",
            requested_service="Humira (adalimumab)",
            decision=Decision.APPROVED,
        )
        doc = (
            "Moderate to severe rheumatoid arthritis. Failed methotrexate "
            "(DMARD). Negative TB screen. Rheumatologist prescribing."
        )
        result = engine.review(case, doc)
        assert result.recommendation is Recommendation.APPROVE
        assert not result.missing_criteria
        assert result.confidence_score > 0.0


class TestInsufficient:
    def test_partial_evidence_is_insufficient(self):
        engine = ClinicalReviewEngine()
        case = PatientCase(
            diagnosis="Rheumatoid arthritis",
            requested_service="Humira (adalimumab)",
            decision=Decision.UNKNOWN,
        )
        result = engine.review(case, "Patient has rheumatoid arthritis.")
        assert result.recommendation is Recommendation.INSUFFICIENT_INFORMATION
        assert result.missing_criteria


class TestNoGuideline:
    def test_unmatched_service_insufficient(self):
        engine = ClinicalReviewEngine()
        case = PatientCase(requested_service="dental cleaning")
        result = engine.review(case)
        assert result.recommendation is Recommendation.INSUFFICIENT_INFORMATION
        assert result.guideline_id is None
        assert result.recommended_actions


class TestContraindication:
    def test_contraindication_denies(self):
        engine = ClinicalReviewEngine()
        case = PatientCase(
            diagnosis="Rheumatoid arthritis",
            requested_service="Humira (adalimumab)",
            decision=Decision.APPROVED,
        )
        doc = (
            "Moderate to severe rheumatoid arthritis. Failed methotrexate. "
            "Negative TB screen. Rheumatologist prescribing. However patient "
            "has an active infection currently."
        )
        result = engine.review(case, doc)
        assert result.recommendation is Recommendation.DENY
        assert result.contraindications_found

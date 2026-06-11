"""Regression tests for azathioprine/thiopurine step-therapy recognition (Case 4).

Conventional-therapy step requirements are not methotrexate-only: a documented
trial and failure of azathioprine (AZA) must satisfy STEP_THERAPY. Bare
azathioprine mentions without trial/failure context must not.
"""

from __future__ import annotations

from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation
from app.review.clinical_nlp import extract_clinical_signals, step_therapy_status
from app.review.engine import ClinicalReviewEngine


def _case() -> PatientCase:
    return PatientCase(
        diagnosis="Moderate to severe rheumatoid arthritis",
        requested_service="Humira (adalimumab)",
        decision=Decision.UNKNOWN,
    )


def _detail(result, criterion_id: str):
    return next(d for d in result.criteria_detail if d.id == criterion_id)


def test_azathioprine_trial_classified_as_step_therapy_signal():
    signals = extract_clinical_signals(
        "Patient historically completed a 14-month trial of oral "
        "Azathioprine (AZA) 150mg daily."
    )
    step = [s for s in signals if s.label == "STEP_THERAPY"]

    assert step
    assert step_therapy_status(step[0]) == "failed"


def test_failed_azathioprine_satisfies_step_therapy():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Completed a 14-month trial of oral azathioprine; therapy was "
            "deemed a clinical failure. TB negative. Rheumatologist "
            "prescribing."
        ),
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is True
    assert result.recommendation is Recommendation.APPROVE


def test_bare_azathioprine_mention_does_not_satisfy_step_therapy():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Azathioprine appears on the medication list. TB negative. "
            "Rheumatologist prescribing."
        ),
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is False
    assert result.recommendation is Recommendation.INSUFFICIENT_INFORMATION

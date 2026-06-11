"""Regression tests for gastroenterology specialist recognition (Case 4).

Applied because the SPECIALIST criterion remained 'unknown' after the
azathioprine step-therapy fix: specialist evaluation recognized only
rheumatology/dermatology, so a prescribing gastroenterologist (the
appropriate specialist for Crohn's disease) produced no evidence.
"""

from __future__ import annotations

from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation
from app.review.engine import ClinicalReviewEngine


def _crohns_case() -> PatientCase:
    return PatientCase(
        diagnosis="Severe Crohn's Disease (K50.90)",
        requested_service="Humira (adalimumab)",
        decision=Decision.UNKNOWN,
    )


def _detail(result, criterion_id: str):
    return next(d for d in result.criteria_detail if d.id == criterion_id)


MARTHA_KENT_TEXT = (
    "PATIENT TRANSFER SUMMARY. The patient has a 5-year history of severe "
    "Crohn's Disease (K50.90) with recurrent fistulizing disease and is "
    "requesting a new prior authorization for Humira 40mg SC every 2 weeks. "
    "Per records transferred from the Midwest Inflammatory Bowel Disease "
    "Center (Dr. J. Kent, MD, Gastroenterology, 2022-2024), the patient "
    "historically completed a 14-month trial of oral Azathioprine (AZA) "
    "150mg daily. Therapy was deemed a clinical failure in late 2024 due to "
    "persistent mucosal ulceration seen on colonoscopy and recurrent flares. "
    "A T-SPOT.TB test was performed by our clinic on 01-Mar-2026. Result: "
    "NEGATIVE. Signature: Dr. Lana Lang, MD. Specialty: Gastroenterology "
    "and Hepatology Fellowship Trained."
)


def test_gastroenterologist_satisfies_specialist_criterion():
    result = ClinicalReviewEngine().review(
        _crohns_case(),
        "Failed azathioprine. TB negative. Gastroenterologist prescribing.",
    )

    specialist = _detail(result, "SPECIALIST")
    assert specialist.met is True


def test_case_4_crohns_transfer_summary_approves():
    result = ClinicalReviewEngine().review(_crohns_case(), MARTHA_KENT_TEXT)

    step = _detail(result, "STEP_THERAPY")
    specialist = _detail(result, "SPECIALIST")
    assert step.met is True
    assert specialist.met is True
    assert result.recommendation is Recommendation.APPROVE


def test_chiropractor_does_not_satisfy_specialist_criterion():
    result = ClinicalReviewEngine().review(
        _crohns_case(),
        "Failed azathioprine. TB negative. Chiropractor prescribing.",
    )

    specialist = _detail(result, "SPECIALIST")
    assert specialist.met is False
    assert result.recommendation is not Recommendation.APPROVE

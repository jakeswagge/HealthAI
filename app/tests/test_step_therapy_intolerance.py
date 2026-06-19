"""Regression tests for the step-therapy intolerance/toxicity exception (Case 3).

A documented serious adverse reaction attributable to methotrexate must
satisfy step therapy without a completed trial. Unattributed adverse events,
negated toxicity mentions, and refusals must NOT pass through this exception.
"""

from __future__ import annotations

from app.models.patient_case import Decision, PatientCase
from app.models.review_result import CriterionStatus, Recommendation
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


CASE_3_TEXT = (
    "Diagnosis: Rheumatoid Arthritis. TB negative. Rheumatologist prescribing. "
    "Patient was initiated on methotrexate 15mg weekly. "
    "At 4-week follow-up, routine safety labs revealed acute hepatic injury "
    "directly attributable to MTX ingestion. Humira requested."
)


def test_mtx_induced_liver_injury_classified_as_intolerance():
    signals = extract_clinical_signals(
        "Routine safety labs revealed acute hepatic injury directly "
        "attributable to MTX ingestion."
    )
    step = [s for s in signals if s.label == "STEP_THERAPY"]

    assert step
    assert step_therapy_status(step[0]) == "intolerance"


def test_case_3_mtx_toxicity_satisfies_step_therapy_and_approves():
    result = ClinicalReviewEngine().review(_case(), CASE_3_TEXT)

    step = _detail(result, "STEP_THERAPY")
    assert step.met is True
    assert "intolerance" in (step.note or "").lower() or "toxicity" in (
        step.note or ""
    ).lower()
    assert result.recommendation is Recommendation.APPROVE


def test_mtx_contraindication_satisfies_step_therapy():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Diagnosis: Rheumatoid Arthritis. TB negative. Rheumatologist "
            "prescribing. Methotrexate is contraindicated in this patient. "
            "Humira requested."
        ),
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is True


def test_unattributed_hepatic_injury_does_not_satisfy_step_therapy():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "TB negative. Rheumatologist prescribing. "
            "Patient remains on methotrexate; labs revealed acute hepatic "
            "injury of unclear etiology."
        ),
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is False
    assert result.recommendation is not Recommendation.APPROVE


def test_negated_mtx_toxicity_does_not_satisfy_step_therapy():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "TB negative. Rheumatologist prescribing. "
            "No evidence of methotrexate-induced toxicity."
        ),
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is False
    assert result.recommendation is not Recommendation.APPROVE


def test_refused_methotrexate_still_fails_step_therapy_after_exception_added():
    result = ClinicalReviewEngine().review(
        _case(),
        "Patient refused methotrexate. TB negative. Rheumatologist prescribing.",
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is False
    assert "refusal" in (step.note or "").lower()
    assert result.recommendation is Recommendation.DENY


def test_renal_failure_bypass_requires_human_review():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Patient: Victor Fries\n"
            "Diagnosis: Moderate to severe Rheumatoid Arthritis\n"
            "Requested Service: Humira.\n"
            "Methotrexate is completely bypassed because the patient has Stage 4 "
            "chronic kidney disease and severe renal failure.\n"
            "TB screening verified negative.\n"
            "Rheumatologist prescribing.\n"
            "Status: DENIED\n"
            "Reason for Denial: Medical exception documentation requires human "
            "specialist review."
        ),
    )

    step = _detail(result, "STEP_THERAPY")
    tb = _detail(result, "TB_SCREEN")
    specialist = _detail(result, "SPECIALIST")

    assert step.met is False
    assert step.status is CriterionStatus.NOT_MET
    assert "medical exception" in (step.note or "").lower()
    assert tb.met is True
    assert specialist.met is True
    assert result.recommendation is Recommendation.DENY
    assert result.safety_gate.get("status") == "HUMAN_REVIEW_REQUIRED"
    assert any(
        "medical exception" in reason.lower() or "human review" in reason.lower()
        for reason in result.safety_gate.get("reasons", [])
    )

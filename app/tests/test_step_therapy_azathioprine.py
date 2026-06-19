"""Regression tests for azathioprine/thiopurine step-therapy recognition (Case 4).

Conventional-therapy step requirements are not methotrexate-only: a documented
trial and failure of azathioprine (AZA) must satisfy STEP_THERAPY. Bare
azathioprine mentions without trial/failure context must not.
"""

from __future__ import annotations

from app.assembly.engine import CaseAssemblyEngine
from app.models.case_document import CaseDocument, DocumentCategory
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


def _enbrel_psoriasis_case() -> PatientCase:
    return PatientCase(
        diagnosis="Severe plaque psoriasis",
        icd10_codes=["L40.0"],
        requested_service="Enbrel (etanercept)",
        decision=Decision.UNKNOWN,
    )


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


def test_assembled_azathioprine_failure_does_not_claim_methotrexate_failure():
    doc = CaseDocument(
        case_id="C-AZA",
        filename="aza-note.txt",
        document_type=DocumentCategory.CLINICAL_NOTE,
        raw_text=(
            "Diagnosis: Rheumatoid Arthritis\n"
            "Requested Service: Humira\n"
            "Completed a 14-month trial of oral azathioprine; therapy was "
            "deemed a clinical failure.\n"
            "TB negative.\n"
            "Rheumatologist prescribing.\n"
        ),
    )

    context = CaseAssemblyEngine().assemble("C-AZA", [doc])
    criterion_refs = [
        ev
        for ev in context.evidence
        if ev.fact_type == "criterion_step_therapy"
    ]

    assert any(
        ev.normalized_fact == "criterion_step_therapy: azathioprine failure"
        for ev in criterion_refs
    )
    assert not any("methotrexate failure" in ev.normalized_fact for ev in criterion_refs)


def test_assembled_methotrexate_failure_keeps_methotrexate_specific_evidence():
    doc = CaseDocument(
        case_id="C-MTX",
        filename="mtx-note.txt",
        document_type=DocumentCategory.CLINICAL_NOTE,
        raw_text=(
            "Diagnosis: Rheumatoid Arthritis\n"
            "Requested Service: Humira\n"
            "Methotrexate failed after 12 weeks.\n"
            "TB negative.\n"
            "Rheumatologist prescribing.\n"
        ),
    )

    context = CaseAssemblyEngine().assemble("C-MTX", [doc])

    assert any(
        ev.fact_type == "criterion_step_therapy"
        and ev.normalized_fact == "criterion_step_therapy: methotrexate failure"
        for ev in context.evidence
    )


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


def test_enbrel_psoriasis_allows_failed_systemic_therapy_step():
    result = ClinicalReviewEngine().review(
        _enbrel_psoriasis_case(),
        (
            "Moderate to severe plaque psoriasis. Failed phototherapy and "
            "systemic therapy. Negative TB (QuantiFERON). Under care of a "
            "dermatologist."
        ),
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is True
    assert "systemic" in (step.note or "").lower() or "phototherapy" in (
        step.note or ""
    ).lower()
    assert result.recommendation is Recommendation.APPROVE


def test_assembled_systemic_therapy_failure_uses_generic_step_phrase():
    doc = CaseDocument(
        case_id="C-PSO",
        filename="psoriasis-note.txt",
        document_type=DocumentCategory.CLINICAL_NOTE,
        raw_text=(
            "Diagnosis: Severe plaque psoriasis\n"
            "Requested Service: Enbrel\n"
            "Failed phototherapy and systemic therapy.\n"
            "TB negative.\n"
            "Dermatologist prescribing.\n"
        ),
    )

    context = CaseAssemblyEngine().assemble("C-PSO", [doc])

    assert any(
        ev.fact_type == "criterion_step_therapy"
        and ev.normalized_fact == "criterion_step_therapy: step therapy failure"
        for ev in context.evidence
    )

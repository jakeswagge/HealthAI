"""Regression tests for the unified clinical fact contract."""

from __future__ import annotations

import pytest

from app.assembly.engine import CaseAssemblyEngine
from app.cases.service import CaseService
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.clinical_fact import (
    ClinicalFactDomain,
    ConflictStatus,
    DiagnosisState,
    StepTherapyState,
    TBScreenState,
)
from app.models.review_result import CriterionStatus, Recommendation
from app.review.engine import ClinicalReviewEngine
from app.storage.database import connect, initialize_schema


def _doc(text: str, *, category=DocumentCategory.CLINICAL_NOTE) -> CaseDocument:
    return CaseDocument(
        case_id="C-FACT",
        filename="clinical.txt",
        document_type=category,
        raw_text=text,
    )


def _detail(result, criterion_id: str):
    return next(d for d in result.criteria_detail if d.id == criterion_id)


def test_positive_tb_clinical_fact_drives_denial_and_not_met_evidence():
    doc = _doc(
        "Diagnosis: Rheumatoid Arthritis\n"
        "Completed 6-month Methotrexate trial and failed.\n"
        "QuantiFERON-TB Gold result: POSITIVE / REACTIVE.\n"
        "PRESCRIBER: Dr. Steve Trevor, MD, Fellow of the American College of Rheumatology.\n"
        "Requested Service: Humira (adalimumab)\n"
    )
    context = CaseAssemblyEngine().assemble("C-FACT", [doc])
    tb_facts = [
        fact for fact in context.clinical_facts
        if fact.domain is ClinicalFactDomain.TB_SCREEN
    ]

    result = ClinicalReviewEngine().review(context.patient_case, doc.raw_text)
    tb = _detail(result, "TB_SCREEN")

    assert any(fact.state == TBScreenState.POSITIVE.value for fact in tb_facts)
    assert result.recommendation is Recommendation.DENY
    assert tb.status is CriterionStatus.NOT_MET
    assert tb.not_met_evidence_ids
    assert any("tuberculosis" in c.lower() for c in result.contraindications_found)


@pytest.mark.parametrize(
    ("text", "state"),
    [
        ("Patient declined methotrexate therapy.", StepTherapyState.REFUSED.value),
        ("Patient never started methotrexate therapy.", StepTherapyState.NEVER_STARTED.value),
        ("Patient is currently taking methotrexate.", StepTherapyState.IN_PROGRESS.value),
    ],
)
def test_step_therapy_clinical_fact_states(text, state):
    doc = _doc(
        "Diagnosis: Rheumatoid Arthritis\n"
        f"{text}\n"
        "Negative PPD TB test.\n"
        "Board Certified Rheumatologist prescribing.\n"
        "Requested Service: Humira (adalimumab)\n"
    )
    context = CaseAssemblyEngine().assemble("C-FACT", [doc])
    states = {
        fact.state
        for fact in context.clinical_facts
        if fact.domain is ClinicalFactDomain.STEP_THERAPY
    }

    assert state in states


def test_non_active_diagnosis_assertions_are_clinical_facts_not_primary_dx():
    doc = _doc(
        "Family history of Rheumatoid Arthritis.\n"
        "History of Psoriatic Arthritis.\n"
        "Diagnosis: Rheumatoid Arthritis\n"
        "Negative PPD TB test. Methotrexate failed. Rheumatologist prescribing.\n"
        "Requested Service: Humira (adalimumab)\n"
    )
    context = CaseAssemblyEngine().assemble("C-FACT", [doc])
    diagnosis_states = {
        fact.state
        for fact in context.clinical_facts
        if fact.domain is ClinicalFactDomain.DIAGNOSIS
    }

    assert DiagnosisState.ACTIVE.value in diagnosis_states
    assert DiagnosisState.FAMILY_HISTORY.value in diagnosis_states
    assert DiagnosisState.HISTORICAL.value in diagnosis_states
    assert context.patient_case.diagnosis == "Rheumatoid Arthritis"


def test_semantic_diagnosis_assertion_conflict_marks_facts():
    doc = _doc(
        "Diagnosis: Rheumatoid Arthritis\n"
        "No Rheumatoid Arthritis is present on today's exam.\n"
        "Requested Service: Humira (adalimumab)\n"
    )
    context = CaseAssemblyEngine().assemble("C-FACT", [doc])
    conflicts = [
        c for c in context.conflict_report.conflicts
        if c.fact_type == "diagnosis"
    ]

    assert conflicts
    assert conflicts[0].clinical_fact_ids
    assert any(
        fact.conflict_status is ConflictStatus.CONFLICTED
        for fact in context.clinical_facts
    )


def test_attach_review_fails_closed_when_criterion_detail_has_no_traceability():
    conn = connect(":memory:")
    initialize_schema(conn)
    try:
        service = CaseService(conn=conn)
        record = service.create_case("traceability.txt")
        service.add_document(
            record.case_id,
            "traceability.txt",
            "Diagnosis: Rheumatoid Arthritis\nRequested Service: Humira\n",
            1,
            "CLINICAL_NOTE",
        )
        service.assemble_case(record.case_id)

        review = ClinicalReviewEngine().review(
            service.get_case(record.case_id).patient_case,
            "Diagnosis: Rheumatoid Arthritis\nRequested Service: Humira\n",
        )
        for detail in review.criteria_detail:
            detail.supporting_evidence_ids = []
            detail.not_met_evidence_ids = []
            detail.missing_evidence = []

        updated = service.attach_review(record.case_id, review)

        assert updated.review_result.safety_gate["status"] == "HUMAN_REVIEW_REQUIRED"
        assert any(
            "traceability" in reason.lower()
            for reason in updated.review_result.safety_gate["reasons"]
        )
    finally:
        conn.close()

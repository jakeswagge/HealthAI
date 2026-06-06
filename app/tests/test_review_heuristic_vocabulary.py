"""Regression tests for deterministic clinical vocabulary coverage."""

from __future__ import annotations

import pytest

from app.assembly.engine import CaseAssemblyEngine
from app.evidence.linker import link_review
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.patient_case import Decision, PatientCase
from app.review.engine import ClinicalReviewEngine


def _case() -> PatientCase:
    return PatientCase(
        diagnosis="Moderate to severe rheumatoid arthritis",
        requested_service="Humira (adalimumab)",
        decision=Decision.UNKNOWN,
    )


def _detail(result, criterion_id: str):
    return next(d for d in result.criteria_detail if d.id == criterion_id)


def _review(doc: str):
    return ClinicalReviewEngine().review(_case(), doc)


@pytest.mark.parametrize(
    "phrase",
    [
        "rheumatology",
        "evaluated by rheumatology",
        "rheumatology clinic",
        "rheumatology consultation",
        "specialist consultation",
        "specialist evaluation",
        "evaluated by specialist",
        "seen by specialist",
        "under care of rheumatology",
        "referred to rheumatology",
        "board-certified rheumatologist",
        "consulting rheumatologist",
        "reviewed by rheumatology service",
    ],
)
def test_specialist_detects_requested_vocabulary(phrase):
    result = _review(
        f"Failed methotrexate. Negative TB screen. Patient {phrase}."
    )

    detail = _detail(result, "SPECIALIST")
    assert detail.met is True
    assert phrase in (detail.note or "").lower()


@pytest.mark.parametrize(
    "phrase",
    [
        "quantiferon",
        "quantiferon gold",
        "quantiferon-tb",
        "t-spot",
        "tb test negative",
        "tuberculosis screening negative",
        "tuberculosis test negative",
        "latent tb screening",
        "negative tb result",
    ],
)
def test_tb_screen_detects_requested_vocabulary(phrase):
    result = _review(
        f"Failed methotrexate. {phrase}. Rheumatologist prescribing."
    )

    detail = _detail(result, "TB_SCREEN")
    assert detail.met is True
    assert phrase in (detail.note or "").lower()


@pytest.mark.parametrize(
    "phrase",
    [
        "failed methotrexate",
        "methotrexate trial",
        "inadequate response to methotrexate",
        "persistent symptoms despite methotrexate",
        "dmard failure",
        "conventional dmard failure",
        "methotrexate ineffective",
        "uncontrolled disease on methotrexate",
        "refractory to methotrexate",
    ],
)
def test_step_therapy_detects_requested_vocabulary(phrase):
    result = _review(
        f"{phrase}. Negative TB screen. Rheumatologist prescribing."
    )

    detail = _detail(result, "STEP_THERAPY")
    assert detail.met is True
    assert phrase in (detail.note or "").lower()


def test_negated_specialist_does_not_satisfy_specialist():
    result = _review(
        "Failed methotrexate. Negative TB screen. "
        "No specialist involvement documented."
    )

    detail = _detail(result, "SPECIALIST")
    assert detail.met is False


def test_negated_tb_screen_does_not_satisfy_tb_screen():
    result = _review(
        "Failed methotrexate. Rheumatologist prescribing. "
        "No tuberculosis screening performed."
    )

    detail = _detail(result, "TB_SCREEN")
    assert detail.met is False


def test_matched_phrase_links_to_source_evidence_reference():
    note = CaseDocument(
        case_id="C-VOCAB",
        filename="rheumatology-note.txt",
        document_type=DocumentCategory.CLINICAL_NOTE,
        raw_text=(
            "Patient Name: John Smith\n"
            "Diagnosis: Moderate to severe rheumatoid arthritis\n"
            "Requested Medication: Humira\n"
            "Inadequate response to methotrexate.\n"
            "Patient evaluated by rheumatology.\n"
        ),
    )
    lab = CaseDocument(
        case_id="C-VOCAB",
        filename="tb-lab.txt",
        document_type=DocumentCategory.LAB_RESULT,
        raw_text="Quantiferon-TB Gold negative.",
    )
    ctx = CaseAssemblyEngine().assemble("C-VOCAB", [note, lab])
    document_text = "\n".join(doc.raw_text for doc in (note, lab))

    review = ClinicalReviewEngine().review(ctx.patient_case, document_text)
    linked = link_review(review, ctx)

    specialist = _detail(linked, "SPECIALIST")
    assert specialist.met is True
    assert "evaluated by rheumatology" in (specialist.note or "").lower()

    linked_ids = set(linked.evidence_refs["matched_criteria"])
    specialist_refs = [
        ev for ev in ctx.evidence
        if ev.fact_type == "criterion_specialist" and ev.evidence_id in linked_ids
    ]
    assert specialist_refs
    assert specialist_refs[0].source_filename == "rheumatology-note.txt"
    assert specialist_refs[0].normalized_fact.endswith("evaluated by rheumatology")

"""Regression tests for the additive AWS-inspired architecture upgrades."""

from __future__ import annotations

from app.assembly.engine import CaseAssemblyEngine
from app.cases.service import CaseService
from app.ingestion.classifier import DocumentClassifier
from app.models.case_document import (
    CaseDocument,
    DocumentCategory,
    DocumentSectionType,
    PAGE_DELIMITER,
)
from app.models.evidence_review_decision import EvidenceDecision
from app.models.review_result import (
    CriterionEvaluation,
    CriterionStatus,
    Recommendation,
    ReviewResult,
)
from app.review.comparison import compare_reviews
from app.review.engine import ClinicalReviewEngine
from app.storage.database import connect, initialize_schema


def _doc(
    text: str,
    *,
    filename: str = "packet.txt",
    category: DocumentCategory = DocumentCategory.OTHER,
    case_id: str = "C-UPGRADE",
) -> CaseDocument:
    return CaseDocument(
        case_id=case_id,
        filename=filename,
        document_type=category,
        page_count=len(text.split(PAGE_DELIMITER)),
        raw_text=text,
    )


def test_document_boundary_detection_creates_page_range_sections():
    packet = PAGE_DELIMITER.join(
        [
            "Patient Name: Jane Smith\nMember ID: JS-1\nInsurance Company: Aetna",
            "Clinical Note\nDiagnosis: Rheumatoid arthritis\nFailed methotrexate.",
            "Laboratory Report\nQuantiferon-TB Gold negative\nReference Range: Negative",
        ]
    )
    sections = DocumentClassifier().detect_sections(_doc(packet, filename="packet.txt"))

    assert [s.section_type for s in sections] == [
        DocumentSectionType.ADMINISTRATIVE,
        DocumentSectionType.CLINICAL_HISTORY,
        DocumentSectionType.LABS,
    ]
    assert [(s.page_start, s.page_end) for s in sections] == [(1, 1), (2, 2), (3, 3)]
    assert all(s.confidence_score > 0 for s in sections)


def test_evidence_extraction_emits_sectioned_prior_auth_denial_facts():
    denial = _doc(
        "Status: DENIED\n"
        "Reason for Denial: Claim denied because no prior authorization number "
        "was on file.",
        filename="denial.txt",
        category=DocumentCategory.DENIAL_LETTER,
    )
    refs = CaseAssemblyEngine().extractor.extract(denial)
    by_fact = {ref.fact_type: ref for ref in refs}

    assert by_fact["prior_auth_status"].normalized_fact.endswith("missing")
    assert "missing prior authorization" in by_fact["claim_denial_reason"].normalized_fact
    assert by_fact["prior_auth_status"].section_label.startswith("claim_or_denial")


def test_assembly_normalized_facts_feed_local_review_without_raw_text():
    clinical = _doc(
        "Patient Name: Jane Smith\n"
        "Member ID: JS-1\n"
        "Diagnosis: Moderate to severe rheumatoid arthritis\n"
        "Requested Medication: Humira\n"
        "Rheumatologist prescribing Humira.\n"
        "Quantiferon-TB Gold negative.\n"
        "Failed methotrexate after 6 months due to inadequate response.",
        filename="clinical-note.txt",
        category=DocumentCategory.CLINICAL_NOTE,
    )

    context = CaseAssemblyEngine().assemble("C-UPGRADE", [clinical])
    normalized = context.patient_case.normalized_fields

    assert normalized["tb_screen_result"].normalized_value == "negative"
    assert normalized["step_therapy_status"].normalized_value == "failed"
    assert normalized["specialist_status"].normalized_value == "documented"

    review = ClinicalReviewEngine().review(context.patient_case)

    assert review.recommendation is Recommendation.APPROVE
    assert all(detail.review_backend == "local" for detail in review.criteria_detail)
    assert all(detail.status is CriterionStatus.MET for detail in review.criteria_detail)


def test_rule_evaluations_include_status_reasoning_confidence_and_backend():
    result = ClinicalReviewEngine().review(
        case=_case_for_review(),
        document_text=(
            "Rheumatoid arthritis. Failed methotrexate. Negative TB screen. "
            "Rheumatologist prescribing."
        ),
    )

    assert result.criteria_detail
    for detail in result.criteria_detail:
        assert detail.status in {
            CriterionStatus.MET,
            CriterionStatus.NOT_MET,
            CriterionStatus.UNKNOWN,
        }
        assert detail.reasoning
        assert detail.confidence_score > 0
        assert detail.review_backend == "local"


def test_compare_mode_distinguishes_material_from_wording_differences():
    local = ReviewResult(
        recommendation=Recommendation.APPROVE,
        matched_criteria=["Diagnosis confirmed"],
        rationale="Local rationale.",
        confidence_score=0.9,
        criteria_detail=[
            CriterionEvaluation(
                id="DX",
                description="Diagnosis confirmed",
                met=True,
                status=CriterionStatus.MET,
            )
        ],
    )
    ai = ReviewResult(
        recommendation=Recommendation.APPROVE,
        matched_criteria=["Diagnosis is confirmed"],
        rationale="AI rationale.",
        confidence_score=0.9,
        criteria_detail=[
            CriterionEvaluation(
                id="DX",
                description="Diagnosis confirmed",
                met=True,
                status=CriterionStatus.MET,
            )
        ],
    )

    harmless = compare_reviews(local, ai)
    assert harmless.requires_human_review is False
    assert harmless.non_material_differences

    ai_deny = ai.model_copy(update={"recommendation": Recommendation.DENY})
    material = compare_reviews(local, ai_deny)
    assert material.requires_human_review is True
    assert material.material_disagreements


def test_review_safety_gate_blocks_invalid_and_rejected_evidence_ids():
    conn = connect(":memory:")
    initialize_schema(conn)
    service = CaseService(conn=conn)
    record = service.create_case("case.txt")
    service.add_document(
        record.case_id,
        "case.txt",
        "Patient Name: Jane Smith\nDiagnosis: Rheumatoid arthritis\nProcedure: Humira",
        document_type=DocumentCategory.CLINICAL_NOTE,
    )
    service.assemble_case(record.case_id)
    evidence_id = service.list_evidence(record.case_id)[0].evidence_id
    service.record_evidence_decision(
        record.case_id,
        evidence_id,
        "Reviewer",
        EvidenceDecision.REJECT,
        "Bad extraction",
    )

    review = ReviewResult(
        recommendation=Recommendation.APPROVE,
        matched_criteria=["Diagnosis confirmed"],
        rationale="Uses cited evidence.",
        confidence_score=0.9,
        matched_evidence_ids=[evidence_id, "EV-DOES-NOT-EXIST"],
    )
    updated = service.attach_review(record.case_id, review)

    gate = updated.review_result.safety_gate
    reasons = " ".join(gate["reasons"]).lower()
    assert updated.status.value == "PENDING_HUMAN_REVIEW"
    assert "do not exist" in reasons
    assert "rejected evidence" in reasons


def _case_for_review():
    from app.models.patient_case import Decision, PatientCase

    return PatientCase(
        diagnosis="Rheumatoid arthritis",
        requested_service="Humira (adalimumab)",
        decision=Decision.DENIED,
    )

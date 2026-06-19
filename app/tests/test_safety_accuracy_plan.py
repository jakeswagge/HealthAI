"""Tests for pilot safety gates, provider routing, and verification."""

from __future__ import annotations

import sqlite3

import pytest

from app.appeals.appeal_agent import AppealGenerationAgent
from app.appeals.verifier import AppealVerifier
from app.assembly.engine import CaseAssemblyEngine
from app.cases.appeal_service import ExportBlockedError
from app.cases.service import CaseService
from app.cases.workqueue import WorkqueueBucket, bucket_for_case
from app.guidelines.repository import get_default_repository
from app.models.case_document import CaseDocument, DocumentCategory
from app.governance.safety import SafetyGate
from app.models.case_record import CaseRecord, CaseStatus, HumanDecision, HumanReviewDecision
from app.models.governance import EvidenceMode, GovernanceComplianceReport, GovernanceSettings
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.models.safety import AppealVerificationStatus, SafetyGateStatus
from app.review.engine import ClinicalReviewEngine
from app.services.provider_router import AITask, get_task_config
from app.storage.database import connect, initialize_schema


@pytest.fixture
def service():
    conn = connect(":memory:")
    initialize_schema(conn)
    svc = CaseService(conn=conn)
    yield svc
    conn.close()


def _case(confidence: float = 0.95) -> PatientCase:
    return PatientCase(
        patient_name="Rachel Green",
        member_id="MES013",
        diagnosis="Rheumatoid Arthritis",
        requested_service="Humira",
        decision=Decision.DENIED,
        confidence_score=confidence,
    )


def _deny_review(confidence: float = 0.9) -> ReviewResult:
    return ReviewResult(
        recommendation=Recommendation.DENY,
        matched_criteria=["Diagnosis confirmed"],
        missing_criteria=["Step therapy"],
        rationale="Step therapy is missing.",
        confidence_score=confidence,
        guideline_id="GL-HUMIRA-001",
        service_name="Humira",
    )


def test_provider_router_uses_task_specific_backend(monkeypatch):
    monkeypatch.setenv("HEALTHAI_STRUCTURED_EXTRACTION_BACKEND", "gemini")
    monkeypatch.setenv("HEALTHAI_CLINICAL_REASONING_BACKEND", "anthropic")

    assert get_task_config(AITask.STRUCTURED_EXTRACTION).provider_name == "gemini"
    assert get_task_config(AITask.CLINICAL_REASONING).provider_name == "anthropic"


def test_low_confidence_extraction_routes_to_human_review(service):
    rec = service.create_case("low-confidence.txt")

    service.attach_extraction(rec.case_id, _case(confidence=0.8))

    record = service.get_case(rec.case_id)
    assert record.patient_case.safety_gate["status"] == (
        SafetyGateStatus.HUMAN_REVIEW_REQUIRED.value
    )


def test_autonomous_denial_routes_to_human_queue(service):
    rec = service.create_case("deny.txt")
    service.attach_extraction(rec.case_id, _case())

    service.attach_review(rec.case_id, _deny_review())

    record = service.get_case(rec.case_id)
    assert record.status is CaseStatus.PENDING_HUMAN_REVIEW
    assert bucket_for_case(record) is WorkqueueBucket.READY_FOR_SIGN_OFF
    assert "Denial recommendation" in " ".join(
        record.review_result.safety_gate["reasons"]
    )


def test_human_review_requires_comments(service):
    rec = service.create_case("comments.txt")
    service.attach_extraction(rec.case_id, _case())
    service.attach_review(rec.case_id, _deny_review())

    with pytest.raises(ValueError, match="comments are required"):
        service.record_human_review(rec.case_id, "Reviewer", HumanDecision.APPROVE, "")


def test_export_blocks_unverified_appeal(service):
    rec = service.create_case("export-block.txt")
    service.attach_extraction(rec.case_id, _case())
    service.attach_review(rec.case_id, _deny_review())
    appeal = AppealGenerationAgent().builder.build(_case(), _deny_review())
    service.attach_appeal(rec.case_id, appeal)
    service.record_human_review(
        rec.case_id,
        "Reviewer",
        HumanDecision.APPROVE,
        "Reviewed and approved for pilot export.",
    )

    with pytest.raises(ExportBlockedError):
        service.mark_exported(rec.case_id)


def test_export_blocks_unresolved_review_conflicts_without_human_decision():
    record = CaseRecord(
        case_id="C-CONFLICT",
        patient_case=_case(),
        review_result=ReviewResult(
            recommendation=Recommendation.APPROVE,
            matched_criteria=["Diagnosis confirmed"],
            rationale="Approved despite conflict.",
            confidence_score=0.9,
            safety_gate={
                "unresolved_conflicts": [
                    "Unresolved clinical fact conflict in DIAGNOSIS (2 fact(s))."
                ]
            },
        ),
    )
    compliance = GovernanceComplianceReport(case_id="C-CONFLICT", mode=EvidenceMode.DRAFT)
    settings = GovernanceSettings(
        require_human_review_before_export=False,
        block_autonomous_denials=False,
        require_verified_appeal_claims=False,
    )

    gate = SafetyGate(settings).export(record, compliance)

    assert gate.blocked
    assert any("unresolved conflicts" in reason.lower() for reason in gate.reasons)


def test_export_conflict_block_clears_after_human_decision():
    record = CaseRecord(
        case_id="C-CONFLICT",
        patient_case=_case(),
        review_result=ReviewResult(
            recommendation=Recommendation.APPROVE,
            matched_criteria=["Diagnosis confirmed"],
            rationale="Approved after review.",
            confidence_score=0.9,
            safety_gate={
                "unresolved_conflicts": [
                    "Unresolved clinical fact conflict in DIAGNOSIS (2 fact(s))."
                ]
            },
        ),
        review_decisions=[
            HumanReviewDecision(
                reviewer_name="Reviewer",
                decision=HumanDecision.APPROVE,
                comments="Conflict reviewed.",
            )
        ],
    )
    compliance = GovernanceComplianceReport(case_id="C-CONFLICT", mode=EvidenceMode.DRAFT)
    settings = GovernanceSettings(
        require_human_review_before_export=False,
        block_autonomous_denials=False,
        require_verified_appeal_claims=False,
    )

    gate = SafetyGate(settings).export(record, compliance)

    assert not gate.blocked


def test_appeal_verifier_corrects_unsupported_claims():
    doc = CaseDocument(
        case_id="C1",
        filename="note.txt",
        document_type=DocumentCategory.CLINICAL_NOTE,
        raw_text="Diagnosis: Rheumatoid Arthritis\nRequested Medication: Humira",
    )
    ctx = CaseAssemblyEngine().assemble("C1", [doc])
    review = ReviewResult(
        recommendation=Recommendation.APPROVE,
        matched_criteria=["Diagnosis confirmed"],
        confidence_score=0.9,
    )
    appeal = AppealGenerationAgent().builder.build(
        ctx.patient_case.model_copy(update={"decision": Decision.DENIED}),
        review,
    )
    appeal.clinical_summary = "Patient failed methotrexate and TB screening is negative."

    verified = AppealVerifier().verify(appeal, ctx)

    assert verified.verification.status is AppealVerificationStatus.CORRECTED
    assert "clinical_summary" in verified.verification.unsupported_claims
    assert "Documentation was not available" in verified.clinical_summary


def test_rachel_green_regression_normalized_local_review_matches_humira():
    text = (
        "Patnt Name: Rachel Green\n"
        "DO B: 05-May-1979\n"
        "Memb# MES013\n"
        "Diaganosis: Rheumatiod Artharitis\n"
        "Drug: Humeria\n"
        "Notes: Methatrexat faild aftr 1 yr. Quant-TB gold neg. "
        "Dr. Geller (Rheumatolgy) apprvs."
    )
    case = PatientCase(
        patient_name="Rachel Green",
        member_id="MES013",
        diagnosis="Rheumatiod Artharitis",
        requested_service="Humeria",
        physician_name="Dr. Geller",
        confidence_score=0.95,
    )
    from app.agents.normalization import normalize_patient_case

    normalized = normalize_patient_case(case)
    review = ClinicalReviewEngine().review(normalized, text)

    assert normalized.diagnosis == "Rheumatoid Arthritis"
    assert normalized.requested_service == "Humira"
    assert normalized.raw_fields["requested_service"] == "Humeria"
    assert normalized.normalized_fields["diagnosis"].normalized_value == (
        "Rheumatoid Arthritis"
    )
    assert review.guideline_id == "GL-HUMIRA-001"
    assert review.recommendation is Recommendation.APPROVE


def test_guideline_repository_retrieves_local_candidates():
    repo = get_default_repository(force_reload=True)
    candidates = repo.retrieve(_case(), limit=3)

    assert candidates
    assert candidates[0]["guideline_id"] == "GL-HUMIRA-001"
    assert candidates[0]["required_criteria"]

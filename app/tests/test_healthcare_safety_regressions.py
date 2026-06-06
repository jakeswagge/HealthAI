"""Healthcare safety regressions for deterministic parsing and appeal gating."""

from __future__ import annotations

import pytest

from app.agents.medical_extraction_agent import MedicalExtractionAgent
from app.appeals.appeal_agent import AppealGenerationAgent, AppealAgentError
from app.appeals.builder import AppealLetterBuilder
from app.assembly.engine import CaseAssemblyEngine
from app.evidence.extractor import EvidenceExtractor
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.services.local_client import LocalHeuristicClient
from app.ui import dashboard


SARAH_JOHNSON_PENDING = """Prior Authorization Intake
Patient Name: Sarah Johnson Date of Birth: 02/14/1980
Member ID: SJ-12345
Diagnosis: Rheumatoid arthritis
Request Status: Pending Review
Requested Medication:
Humira
Appeal rights apply if this request is denied after clinical review.
"""

SARAH_JOHNSON_REVIEW_PROSE = """Structured Extraction Review
Patient Name: Sarah Johnson
Member ID: UHC123456
Date of Birth: 1985-03-15
Requested Service: Based on the review, the requested service appears to meet the applicable medical-necessity criteria
Insurance Company: Documentation was not available
Request Status: Pending Review
"""


def _doc(text: str, document_type: DocumentCategory = DocumentCategory.PRIOR_AUTH_FORM):
    return CaseDocument(
        case_id="CASE-SARAH",
        filename="sarah_johnson.txt",
        document_type=document_type,
        raw_text=text,
    )


def _facts(text: str) -> dict[str, list[str]]:
    refs = EvidenceExtractor().extract(_doc(text))
    facts: dict[str, list[str]] = {}
    for ref in refs:
        facts.setdefault(ref.fact_type or "", []).append(
            ref.normalized_fact.split(": ", 1)[-1]
        )
    return facts


def test_sarah_johnson_evidence_fields_are_boundary_safe():
    facts = _facts(SARAH_JOHNSON_PENDING)

    assert facts["patient_name"] == ["Sarah Johnson"]
    assert facts["date_of_birth"] == ["02/14/1980"]
    assert facts["requested_service"] == ["Humira"]
    assert facts["decision"] == ["pending"]
    assert "Sarah Johnson Date of Birth" not in facts["patient_name"][0]


def test_local_heuristic_parses_sarah_johnson_without_pollution():
    case = MedicalExtractionAgent(llm_client=LocalHeuristicClient()).extract(
        SARAH_JOHNSON_PENDING
    ).case

    assert case.patient_name == "Sarah Johnson"
    assert case.date_of_birth == "02/14/1980"
    assert case.requested_service == "Humira"
    assert case.decision is Decision.PENDING


def test_review_prose_is_not_extracted_as_requested_service():
    case = MedicalExtractionAgent(llm_client=LocalHeuristicClient()).extract(
        SARAH_JOHNSON_REVIEW_PROSE
    ).case
    facts = _facts(SARAH_JOHNSON_REVIEW_PROSE)

    assert case.requested_service is None
    assert "requested_service" not in facts
    assert case.decision is Decision.PENDING


def test_assembly_heals_humira_from_traceable_document_text():
    text = """Clinical Note
Patient Name: Sarah Johnson
Member ID: SJ-12345
Diagnosis: Rheumatoid arthritis
Plan: Initiate Humira (adalimumab) due to persistent symptoms.
"""
    ctx = CaseAssemblyEngine().assemble(
        "CASE-SARAH",
        [_doc(text, DocumentCategory.CLINICAL_NOTE)],
    )

    assert ctx.patient_case.requested_service == "Humira (adalimumab)"
    assert "requested_service" in ctx.patient_case.field_sources
    assert any(
        ev.fact_type == "requested_service" and "Humira" in ev.quoted_text
        for ev in ctx.evidence
    )
    assert not any("requested_service" in item for item in ctx.missing_information)


def test_appeal_generation_blocks_without_active_denial():
    case = PatientCase(
        patient_name="Sarah Johnson",
        requested_service="Humira",
        decision=Decision.PENDING,
    )
    review = ReviewResult(
        recommendation=Recommendation.INSUFFICIENT_INFORMATION,
        rationale="Need more documentation.",
    )

    with pytest.raises(AppealAgentError, match="No active insurance denial"):
        AppealGenerationAgent(llm_client=LocalHeuristicClient()).generate(case, review)


def test_pending_decision_renders_as_pending(monkeypatch):
    rendered: list[tuple[str, str]] = []

    class Column:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "markdown", lambda text: rendered.append(("markdown", text)))
    monkeypatch.setattr(dashboard.st, "write", lambda text: rendered.append(("write", text)))
    monkeypatch.setattr(dashboard.st, "columns", lambda count: [Column() for _ in range(count)])
    monkeypatch.setattr(dashboard.st, "error", lambda text: rendered.append(("error", text)))
    monkeypatch.setattr(dashboard.st, "success", lambda text: rendered.append(("success", text)))
    monkeypatch.setattr(dashboard.st, "warning", lambda text: rendered.append(("warning", text)))
    monkeypatch.setattr(dashboard.st, "info", lambda text: rendered.append(("info", text)))

    dashboard._render_patient_summary(
        PatientCase(patient_name="Sarah Johnson", decision=Decision.PENDING)
    )

    assert ("info", "Decision: PENDING") in rendered
    assert ("info", "Decision: UNKNOWN") not in rendered


def test_insufficient_review_outputs_incomplete_package_not_medical_necessity():
    case = PatientCase(
        patient_name="Sarah Johnson",
        requested_service="Humira",
        decision=Decision.DENIED,
        denial_reason="Clinical documentation incomplete.",
    )
    review = ReviewResult(
        recommendation=Recommendation.INSUFFICIENT_INFORMATION,
        missing_criteria=["TB screening"],
        missing_evidence=["TB screening result"],
        rationale="Insufficient information to determine medical necessity.",
    )

    appeal = AppealLetterBuilder().build(case, review)

    assert "Incomplete Case Package" in appeal.letter_text
    assert "supports medical necessity" not in appeal.letter_text.lower()
    assert "meets guideline criteria" not in appeal.letter_text.lower()
    assert appeal.missing_information

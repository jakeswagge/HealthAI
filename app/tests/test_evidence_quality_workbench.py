"""Tests for Milestone 10: Claude evidence extraction, quality, workbench."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.cases.export import build_export_files, build_export_zip
from app.cases.service import CaseService
from app.evidence_ai.extractor import ClaudeEvidenceExtractor
from app.models.audit_event import AuditEventType
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.evidence_reference import EvidenceReference
from app.models.evidence_review_decision import EvidenceDecision
from app.quality.engine import EvidenceQualityEngine
from app.services.local_client import LocalHeuristicClient
from app.services.mock_claude_client import MockClaudeClient
from app.storage.database import connect, initialize_schema


DENIAL_TEXT = (
    "Member Name: Harold Greene\nMember ID: WP-558210334\n"
    "Diagnosis: Rheumatoid arthritis\nProcedure: Humira (adalimumab)\n"
    "ICD-10: M06.9\nStatus: DENIED\n"
    "Reason for Denial: Step therapy not met; no DMARD trial documented."
)
NOTE_TEXT = "Patient: Harold Greene\nDiagnosis: Osteoarthritis"
LAB_TEXT = "Member ID: WP-558210334\nRheumatoid Factor: 85 HIGH"


@pytest.fixture
def conn():
    c = connect(":memory:")
    initialize_schema(c)
    yield c
    c.close()


@pytest.fixture
def service(conn):
    return CaseService(conn=conn)


def _doc(text: str, filename="denial.txt") -> CaseDocument:
    return CaseDocument(case_id="C1", filename=filename,
                        document_type=DocumentCategory.DENIAL_LETTER, raw_text=text)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
class TestSchema:
    def test_new_tables_exist(self, conn):
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"evidence_quality", "evidence_review_decisions"} <= names

    def test_existing_tables_preserved(self, conn):
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"cases", "evidence_references", "ocr_results",
                "authoritative_facts"} <= names


# --------------------------------------------------------------------------- #
# Claude evidence extractor
# --------------------------------------------------------------------------- #
def _evidence_payload() -> dict:
    return {
        "evidence": [
            {"fact_type": "diagnosis", "value": "Rheumatoid arthritis",
             "quoted_text": "Diagnosis: Rheumatoid arthritis",
             "page_number": 1, "confidence": 0.95},
            {"fact_type": "member_id", "value": "WP-558210334",
             "quoted_text": "Member ID: WP-558210334",
             "page_number": 1, "confidence": 0.9},
        ]
    }


def _fabricated_payload() -> dict:
    return {
        "evidence": [
            {"fact_type": "diagnosis", "value": "Lupus",
             "quoted_text": "Diagnosis: Systemic lupus erythematosus",  # NOT in source
             "page_number": 1, "confidence": 0.95},
        ]
    }


class TestClaudeEvidenceExtractor:
    def test_offline_falls_back_to_regex(self):
        ext = ClaudeEvidenceExtractor(llm_client=LocalHeuristicClient())
        refs = ext.extract(_doc(DENIAL_TEXT))
        assert refs  # deterministic extractor produced evidence
        assert ext.used_ai is False

    def test_claude_extraction_with_mock(self):
        client = MockClaudeClient("valid", base_case=_evidence_payload())
        ext = ClaudeEvidenceExtractor(llm_client=client)
        refs = ext.extract(_doc(DENIAL_TEXT))
        assert any(r.fact_type == "diagnosis" for r in refs)
        for r in refs:
            assert r.quoted_text  # always include quoted source text
            assert r.source_document_id

    def test_markdown_wrapped_parsed(self):
        client = MockClaudeClient("markdown_json", base_case=_evidence_payload())
        ext = ClaudeEvidenceExtractor(llm_client=client)
        refs = ext.extract(_doc(DENIAL_TEXT))
        assert refs

    def test_anti_fabrication_gate_drops_unverbatim_quotes(self):
        # The fabricated quote is not present in the document -> dropped ->
        # extractor falls back to the deterministic extractor (still safe).
        client = MockClaudeClient("valid", base_case=_fabricated_payload())
        ext = ClaudeEvidenceExtractor(llm_client=client)
        refs = ext.extract(_doc(DENIAL_TEXT))
        # No reference should claim 'Lupus' (it was fabricated / not in source).
        assert all("lupus" not in r.normalized_fact.lower() for r in refs)

    def test_invalid_json_falls_back(self):
        client = MockClaudeClient(["invalid_json", "invalid_json"],
                                  base_case=_evidence_payload())
        ext = ClaudeEvidenceExtractor(llm_client=client, max_retries=2)
        refs = ext.extract(_doc(DENIAL_TEXT))
        # Falls back to deterministic extractor rather than raising.
        assert refs

    def test_quotes_are_verbatim_from_source(self):
        client = MockClaudeClient("valid", base_case=_evidence_payload())
        ext = ClaudeEvidenceExtractor(llm_client=client)
        refs = ext.extract(_doc(DENIAL_TEXT))
        source_norm = " ".join(DENIAL_TEXT.lower().split())
        for r in refs:
            assert " ".join(r.quoted_text.lower().split()) in source_norm


# --------------------------------------------------------------------------- #
# Quality engine
# --------------------------------------------------------------------------- #
class TestQualityEngine:
    def _refs(self):
        return [
            EvidenceReference(case_id="C1", source_document_id="D1",
                              source_filename="denial.txt", page_number=1,
                              quoted_text="Diagnosis: Rheumatoid arthritis",
                              normalized_fact="diagnosis: Rheumatoid arthritis",
                              fact_type="diagnosis", confidence_score=0.9),
            EvidenceReference(case_id="C1", source_document_id="D2",
                              source_filename="note.txt", page_number=1,
                              quoted_text="Diagnosis: Osteoarthritis",
                              normalized_fact="diagnosis: Osteoarthritis",
                              fact_type="diagnosis", confidence_score=0.8),
        ]

    def test_scores_present(self):
        engine = EvidenceQualityEngine()
        assessments = engine.assess_all(self._refs(), case_id="C1")
        assert len(assessments) == 2
        for a in assessments:
            assert 0.0 <= a.overall_score <= 1.0
            assert isinstance(a, EvidenceQualityAssessment)

    def test_conflicting_support_detected(self):
        engine = EvidenceQualityEngine()
        assessments = engine.assess_all(self._refs(), case_id="C1")
        issues = " ".join(i for a in assessments for i in a.issues).lower()
        assert "conflicting support" in issues

    def test_missing_support_detected(self):
        engine = EvidenceQualityEngine()
        ref = EvidenceReference(case_id="C1", source_document_id="D1",
                                quoted_text="", normalized_fact="diagnosis: X",
                                fact_type="diagnosis")
        a = engine.assess_all([ref], case_id="C1")[0]
        assert any("missing support" in i for i in a.issues)

    def test_duplicate_detected(self):
        engine = EvidenceQualityEngine()
        ref = EvidenceReference(case_id="C1", source_document_id="D1",
                                quoted_text="Member ID: WP-1",
                                normalized_fact="member_id: WP-1",
                                fact_type="member_id", confidence_score=0.9)
        ref2 = ref.model_copy(update={"evidence_id": "EV-2", "source_document_id": "D2"})
        assessments = engine.assess_all([ref, ref2], case_id="C1")
        issues = " ".join(i for a in assessments for i in a.issues).lower()
        assert "duplicate evidence" in issues

    def test_weak_evidence_flagged(self):
        engine = EvidenceQualityEngine()
        ref = EvidenceReference(case_id="C1", source_document_id="",
                                quoted_text="", normalized_fact="",
                                fact_type=None, confidence_score=0.0)
        a = engine.assess_all([ref], case_id="C1")[0]
        assert a.is_weak
        assert any("weak evidence" in i for i in a.issues)

    def test_unsupported_appeal_statements(self):
        from app.models.appeal_letter import AppealLetter
        engine = EvidenceQualityEngine()
        appeal = AppealLetter(appeal_id="A1", clinical_summary="Patient has RA.",
                              appeal_reason="Challenge denial.",
                              section_evidence={})  # no evidence ids
        unsupported = engine.unsupported_appeal_statements(appeal, self._refs())
        assert "clinical_summary" in unsupported
        assert "appeal_reason" in unsupported


# --------------------------------------------------------------------------- #
# Reviewer workbench + decisions
# --------------------------------------------------------------------------- #
class TestReviewerWorkbench:
    def _case_with_evidence(self, service):
        rec = service.create_case("wb case")
        service.ingest_document(rec.case_id, "denial.png", DENIAL_TEXT.encode())
        service.ingest_document(rec.case_id, "note.png", NOTE_TEXT.encode())
        service.assemble_case(rec.case_id)
        service.score_evidence(rec.case_id)
        return rec.case_id

    def test_build_views_with_quality(self, service):
        case_id = self._case_with_evidence(service)
        views = service.build_evidence_views(case_id)
        assert views
        assert all(v.quality is not None for v in views)
        assert all(v.status == "PENDING" for v in views)

    def test_views_show_conflicting(self, service):
        case_id = self._case_with_evidence(service)
        views = service.build_evidence_views(case_id)
        dx_views = [v for v in views if v.evidence.fact_type == "diagnosis"]
        # The two diagnosis values conflict, so each should list a conflict.
        assert any(v.conflicting for v in dx_views)

    def test_record_approve_reject_flag(self, service):
        case_id = self._case_with_evidence(service)
        evidence = service.list_evidence(case_id)
        e0, e1, e2 = evidence[0], evidence[1], evidence[2]
        service.record_evidence_decision(case_id, e0.evidence_id, "Rev", EvidenceDecision.APPROVE)
        service.record_evidence_decision(case_id, e1.evidence_id, "Rev", EvidenceDecision.REJECT, "bad")
        service.record_evidence_decision(case_id, e2.evidence_id, "Rev", "FLAG", "check")
        decisions = service.list_evidence_decisions(case_id)
        assert len(decisions) == 3

    def test_reject_excluded_from_approved(self, service):
        case_id = self._case_with_evidence(service)
        evidence = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evidence[0].evidence_id, "Rev", "REJECT")
        approved = service.approved_evidence(case_id)
        assert evidence[0].evidence_id not in {e.evidence_id for e in approved}
        assert len(approved) == len(evidence) - 1

    def test_decision_requires_reviewer(self, service):
        case_id = self._case_with_evidence(service)
        ev = service.list_evidence(case_id)[0]
        with pytest.raises(ValueError):
            service.record_evidence_decision(case_id, ev.evidence_id, "", "APPROVE")

    def test_decision_audited(self, service):
        case_id = self._case_with_evidence(service)
        ev = service.list_evidence(case_id)[0]
        service.record_evidence_decision(case_id, ev.evidence_id, "Rev", "APPROVE")
        types = [e.event_type for e in service.history(case_id)]
        assert AuditEventType.EVIDENCE_REVIEW_DECISION in types

    def test_quality_scoring_audited(self, service):
        case_id = self._case_with_evidence(service)
        types = [e.event_type for e in service.history(case_id)]
        assert AuditEventType.EVIDENCE_QUALITY_SCORED in types


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
class TestExport:
    def _scored_case(self, service):
        rec = service.create_case("export case")
        service.ingest_document(rec.case_id, "denial.png", DENIAL_TEXT.encode())
        service.assemble_case(rec.case_id)
        service.score_evidence(rec.case_id)
        ev = service.list_evidence(rec.case_id)[0]
        service.record_evidence_decision(rec.case_id, ev.evidence_id, "Rev", "APPROVE")
        return rec.case_id

    def test_export_includes_m10_files(self, service):
        case_id = self._scored_case(service)
        files = build_export_files(
            service.get_case(case_id), service.history(case_id),
            evidence_quality=service.list_evidence_quality(case_id),
            evidence_review_decisions=service.list_evidence_decisions(case_id),
        )
        assert "evidence_quality.json" in files
        assert "evidence_review_decisions.json" in files
        q = json.loads(files["evidence_quality.json"])
        assert q and "overall_score" in q[0]

    def test_export_zip_contains_m10_files(self, service):
        case_id = self._scored_case(service)
        data = build_export_zip(
            service.get_case(case_id), service.history(case_id),
            evidence_quality=service.list_evidence_quality(case_id),
            evidence_review_decisions=service.list_evidence_decisions(case_id),
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert {"evidence_quality.json", "evidence_review_decisions.json"} <= names

    def test_backward_compatible_export(self, service):
        rec = service.create_case("plain")
        files = build_export_files(service.get_case(rec.case_id), service.history(rec.case_id))
        assert "evidence_quality.json" not in files
        assert set(files) == {
            "case_summary.md", "patient_case.json", "review_result.json",
            "appeal_letter.md", "audit_log.json",
        }


# --------------------------------------------------------------------------- #
# Success criterion
# --------------------------------------------------------------------------- #
class TestSuccessCriterion:
    def test_three_docs_extract_score_validate_use(self, service):
        from app.review.review_agent import GuidelineReviewAgent
        from app.appeals.appeal_agent import AppealGenerationAgent

        rec = service.create_case("m10 success")
        service.ingest_document(rec.case_id, "denial.png", DENIAL_TEXT.encode())
        service.ingest_document(rec.case_id, "note.png", NOTE_TEXT.encode())
        service.ingest_document(rec.case_id, "lab.png", LAB_TEXT.encode())
        ctx = service.assemble_case(rec.case_id)

        # Score quality + identify weak evidence.
        assessments = service.score_evidence(rec.case_id)
        assert assessments
        assert service.evidence_quality.count_for_case(rec.case_id) == len(assessments)

        # Reviewer validates: reject one piece of evidence.
        ev = service.list_evidence(rec.case_id)[0]
        service.record_evidence_decision(rec.case_id, ev.evidence_id, "Dr. Smith", "REJECT", "low quality")
        approved = service.approved_evidence(rec.case_id)
        assert len(approved) < len(service.list_evidence(rec.case_id))

        # Approved evidence still drives review + appeal (case unaffected by reject
        # of a single ref here, but workflow remains operational).
        review = GuidelineReviewAgent(llm_client=LocalHeuristicClient()).review(ctx.patient_case).result
        service.attach_review(rec.case_id, review)
        appeal = AppealGenerationAgent(llm_client=LocalHeuristicClient()).generate(ctx.patient_case, review).appeal
        service.attach_appeal(rec.case_id, appeal)
        assert appeal.has_letter

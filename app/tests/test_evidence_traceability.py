"""Tests for evidence linking (traceable review + appeal) and repositories."""

from __future__ import annotations

import json
import io
import zipfile

import pytest

from app.appeals.appeal_agent import AppealGenerationAgent
from app.assembly.engine import CaseAssemblyEngine
from app.cases.document_repository import CaseDocumentRepository
from app.cases.export import build_export_files, build_export_zip
from app.cases.service import CaseService
from app.evidence.linker import link_appeal, link_review
from app.evidence.repository import EvidenceRepository
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.case_record import HumanDecision
from app.review.review_agent import GuidelineReviewAgent
from app.services.local_client import LocalHeuristicClient
from app.storage.database import connect, initialize_schema


@pytest.fixture
def conn():
    c = connect(":memory:")
    initialize_schema(c)
    yield c
    c.close()


DENIAL = CaseDocument(
    case_id="C1", filename="denial.txt", document_type=DocumentCategory.DENIAL_LETTER,
    raw_text=(
        "Member Name: Harold Greene\nMember ID: WP-558210334\n"
        "Diagnosis: Rheumatoid arthritis\nProcedure: Humira (adalimumab)\n"
        "ICD-10: M06.9\nStatus: DENIED\n"
        "Reason for Denial: Step therapy not met; no DMARD (methotrexate) trial documented."
    ),
)
NOTE = CaseDocument(
    case_id="C1", filename="note.txt", document_type=DocumentCategory.CLINICAL_NOTE,
    raw_text=(
        "Patient: Harold Greene\nDiagnosis: Rheumatoid arthritis\n"
        "History: Completed methotrexate (DMARD) trial with failure. Negative TB screen."
    ),
)
LAB = CaseDocument(
    case_id="C1", filename="lab.txt", document_type=DocumentCategory.LAB_RESULT,
    raw_text="Member ID: WP-558210334\nRheumatoid Factor: 85 HIGH\nQuantiFERON-TB: NEGATIVE",
)


# --------------------------------------------------------------------------- #
# Repositories
# --------------------------------------------------------------------------- #
class TestDocumentRepository:
    def test_add_and_query(self, conn):
        repo = CaseDocumentRepository(conn=conn)
        repo.add(DENIAL)
        repo.add(NOTE)
        docs = repo.for_case("C1")
        assert len(docs) == 2
        assert repo.count_for_case("C1") == 2
        assert repo.get(DENIAL.document_id).filename == "denial.txt"

    def test_classification_roundtrip(self, conn):
        repo = CaseDocumentRepository(conn=conn)
        repo.add(DENIAL)
        loaded = repo.get(DENIAL.document_id)
        assert loaded.document_type is DocumentCategory.DENIAL_LETTER


class TestEvidenceRepository:
    def test_persist_and_query(self, conn):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE, LAB])
        repo = EvidenceRepository(conn=conn)
        repo.replace_for_case("C1", ctx.evidence)
        loaded = repo.for_case("C1")
        assert len(loaded) == len(ctx.evidence)
        # Round-trip preserves source attribution.
        sample = loaded[0]
        assert sample.source_filename in {"denial.txt", "note.txt", "lab.txt"}
        assert sample.page_number >= 1

    def test_replace_is_idempotent(self, conn):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE])
        repo = EvidenceRepository(conn=conn)
        repo.replace_for_case("C1", ctx.evidence)
        first = repo.count_for_case("C1")
        repo.replace_for_case("C1", ctx.evidence)
        assert repo.count_for_case("C1") == first

    def test_for_document(self, conn):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE])
        repo = EvidenceRepository(conn=conn)
        repo.replace_for_case("C1", ctx.evidence)
        denial_ev = repo.for_document(DENIAL.document_id)
        assert denial_ev
        assert all(e.source_document_id == DENIAL.document_id for e in denial_ev)


# --------------------------------------------------------------------------- #
# Traceable review + appeal
# --------------------------------------------------------------------------- #
class TestTraceableReviewAndAppeal:
    def _context(self):
        return CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE, LAB])

    def test_review_gets_evidence_refs(self):
        ctx = self._context()
        review = GuidelineReviewAgent(llm_client=LocalHeuristicClient()).review(
            ctx.patient_case
        ).result
        linked = link_review(review, ctx)
        assert linked.evidence_refs  # non-empty
        # Every referenced id must exist in the inventory.
        valid_ids = {e.evidence_id for e in ctx.evidence}
        for ids in linked.evidence_refs.values():
            for eid in ids:
                assert eid in valid_ids

    def test_appeal_sections_traceable(self):
        ctx = self._context()
        review = GuidelineReviewAgent(llm_client=LocalHeuristicClient()).review(
            ctx.patient_case
        ).result
        appeal = AppealGenerationAgent(llm_client=LocalHeuristicClient()).generate(
            ctx.patient_case, review
        ).appeal
        linked, unsupported = link_appeal(appeal, ctx)
        # The clinical summary / appeal reason should be evidence-backed here.
        assert linked.section_evidence
        valid_ids = {e.evidence_id for e in ctx.evidence}
        for ids in linked.section_evidence.values():
            for eid in ids:
                assert eid in valid_ids

    def test_no_unsupported_when_evidence_present(self):
        ctx = self._context()
        review = GuidelineReviewAgent(llm_client=LocalHeuristicClient()).review(
            ctx.patient_case
        ).result
        appeal = AppealGenerationAgent(llm_client=LocalHeuristicClient()).generate(
            ctx.patient_case, review
        ).appeal
        _, unsupported = link_appeal(appeal, ctx)
        assert unsupported == []


# --------------------------------------------------------------------------- #
# Export with evidence
# --------------------------------------------------------------------------- #
class TestTraceableExport:
    def test_export_includes_traceability_files(self, conn):
        service = CaseService(conn=conn)
        rec = service.create_case("multidoc")
        service.add_document(rec.case_id, DENIAL.filename, DENIAL.raw_text, 1, "DENIAL_LETTER")
        service.add_document(rec.case_id, NOTE.filename, NOTE.raw_text, 1, "CLINICAL_NOTE")
        service.add_document(rec.case_id, LAB.filename, LAB.raw_text, 1, "LAB_RESULT")
        ctx = service.assemble_case(rec.case_id)

        record = service.get_case(rec.case_id)
        events = service.history(rec.case_id)
        files = build_export_files(
            record, events, evidence=ctx.evidence, conflict_report=ctx.conflict_report
        )
        assert "evidence_inventory.json" in files
        assert "conflict_report.json" in files
        assert "traceability_report.md" in files
        # Evidence references survive export.
        inv = json.loads(files["evidence_inventory.json"])
        assert len(inv) == len(ctx.evidence)
        assert all("source_filename" in row for row in inv)

    def test_export_zip_contains_traceability(self, conn):
        service = CaseService(conn=conn)
        rec = service.create_case("multidoc")
        service.add_document(rec.case_id, DENIAL.filename, DENIAL.raw_text, 1, "DENIAL_LETTER")
        ctx = service.assemble_case(rec.case_id)
        record = service.get_case(rec.case_id)
        events = service.history(rec.case_id)
        data = build_export_zip(
            record, events, evidence=ctx.evidence, conflict_report=ctx.conflict_report
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert {"evidence_inventory.json", "conflict_report.json", "traceability_report.md"} <= names

    def test_backward_compatible_export_without_evidence(self, conn):
        # Old-style export (no evidence) still yields exactly the 5 core files.
        service = CaseService(conn=conn)
        rec = service.create_case("single")
        record = service.get_case(rec.case_id)
        events = service.history(rec.case_id)
        files = build_export_files(record, events)
        assert set(files) == {
            "case_summary.md",
            "patient_case.json",
            "review_result.json",
            "appeal_letter.md",
            "audit_log.json",
        }


# --------------------------------------------------------------------------- #
# Full multi-document workflow through the service
# --------------------------------------------------------------------------- #
class TestMultiDocWorkflow:
    def test_assemble_persists_and_audits(self, conn):
        service = CaseService(conn=conn)
        rec = service.create_case("multidoc")
        service.add_document(rec.case_id, DENIAL.filename, DENIAL.raw_text, 1, "DENIAL_LETTER")
        service.add_document(rec.case_id, NOTE.filename, NOTE.raw_text, 1, "CLINICAL_NOTE")
        ctx = service.assemble_case(rec.case_id)

        # Documents + evidence persisted.
        assert len(service.list_documents(rec.case_id)) == 2
        assert service.evidence.count_for_case(rec.case_id) == len(ctx.evidence)
        # Case advanced + patient case attached.
        updated = service.get_case(rec.case_id)
        assert updated.patient_case is not None
        assert updated.status.value == "EXTRACTED"
        # Audit recorded the assembly.
        from app.models.audit_event import AuditEventType
        types = [e.event_type for e in service.history(rec.case_id)]
        assert AuditEventType.EXTRACTION_COMPLETED in types

    def test_success_criterion_three_docs(self, conn):
        """denial + note + lab -> assemble, evidence, conflicts, traceable review+appeal."""
        service = CaseService(conn=conn)
        rec = service.create_case("multidoc")
        service.add_document(rec.case_id, DENIAL.filename, DENIAL.raw_text, 1, "DENIAL_LETTER")
        service.add_document(rec.case_id, NOTE.filename, NOTE.raw_text, 1, "CLINICAL_NOTE")
        service.add_document(rec.case_id, LAB.filename, LAB.raw_text, 1, "LAB_RESULT")
        ctx = service.assemble_case(rec.case_id)

        review = GuidelineReviewAgent(llm_client=LocalHeuristicClient()).review(ctx.patient_case).result
        link_review(review, ctx)
        appeal = AppealGenerationAgent(llm_client=LocalHeuristicClient()).generate(ctx.patient_case, review).appeal
        _, unsupported = link_appeal(appeal, ctx)

        service.attach_review(rec.case_id, review)
        service.attach_appeal(rec.case_id, appeal)

        assert review.evidence_refs
        assert appeal.section_evidence
        assert unsupported == []
        assert service.get_case(rec.case_id).status.value == "PENDING_HUMAN_REVIEW"

"""Tests for Milestone 5: case management, audit, metrics, export.

All tests use a shared in-memory SQLite connection so they are fast, isolated,
and never touch the on-disk database.
"""

from __future__ import annotations

import json
import zipfile
import io

import pytest

from app.audit.repository import AuditRepository
from app.cases.export import build_export_files, build_export_zip
from app.cases.repository import CaseRepository, new_case_id
from app.cases.service import CaseService
from app.cases.transitions import InvalidTransitionError, can_transition
from app.metrics.collector import MetricsCollector
from app.models.appeal_letter import AppealLetter
from app.models.audit_event import AuditEventType, AuditActor
from app.models.case_record import (
    CaseRecord,
    CaseStatus,
    HumanDecision,
)
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.storage.database import connect, initialize_schema


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def conn():
    c = connect(":memory:")
    initialize_schema(c)
    yield c
    c.close()


@pytest.fixture
def service(conn):
    return CaseService(conn=conn)


def _sample_case() -> PatientCase:
    return PatientCase(
        patient_name="Harold T. Greene",
        member_id="WP-558210334",
        diagnosis="Moderate to severe rheumatoid arthritis",
        icd10_codes=["M06.9"],
        requested_service="Humira (adalimumab)",
        cpt_codes=["J0135"],
        insurance_company="WellPoint National Insurance",
        decision=Decision.DENIED,
        denial_reason="Step therapy not met: no DMARD trial.",
        confidence_score=0.9,
    )


def _sample_review() -> ReviewResult:
    return ReviewResult(
        recommendation=Recommendation.DENY,
        matched_criteria=["Diagnosis confirmed"],
        missing_criteria=["Step therapy with a DMARD"],
        rationale="Step therapy not documented.",
        confidence_score=0.85,
        guideline_id="GL-HUMIRA-001",
        service_name="Humira (adalimumab)",
        missing_evidence=["DMARD trial records"],
        recommended_actions=["Submit DMARD documentation"],
    )


def _sample_appeal() -> AppealLetter:
    return AppealLetter(
        appeal_id="APL-TEST123",
        patient_name="Harold T. Greene",
        member_id="WP-558210334",
        insurance_company="WellPoint National Insurance",
        requested_service="Humira (adalimumab)",
        original_decision="denied",
        appeal_reason="Challenge step therapy.",
        letter_text="# Appeal\n\n## Patient Information\n...\n## Signature\n",
        confidence_score=0.7,
    )


# --------------------------------------------------------------------------- #
# Repository round-trip
# --------------------------------------------------------------------------- #
class TestCaseRepository:
    def test_create_and_get(self, conn):
        repo = CaseRepository(conn=conn)
        record = CaseRecord(case_id=new_case_id(), patient_case=_sample_case())
        repo.create(record)
        loaded = repo.get(record.case_id)
        assert loaded is not None
        assert loaded.patient_case.patient_name == "Harold T. Greene"
        assert loaded.status is CaseStatus.NEW

    def test_save_upsert_updates(self, conn):
        repo = CaseRepository(conn=conn)
        record = CaseRecord(case_id=new_case_id())
        repo.save(record)  # insert
        record.status = CaseStatus.EXTRACTED
        record.patient_case = _sample_case()
        repo.save(record)  # update
        loaded = repo.get(record.case_id)
        assert loaded.status is CaseStatus.EXTRACTED
        assert loaded.patient_case is not None

    def test_roundtrip_preserves_all_artifacts(self, conn):
        repo = CaseRepository(conn=conn)
        record = CaseRecord(
            case_id=new_case_id(),
            patient_case=_sample_case(),
            review_result=_sample_review(),
            appeal_letter=_sample_appeal(),
        )
        repo.create(record)
        loaded = repo.get(record.case_id)
        assert loaded.review_result.recommendation is Recommendation.DENY
        assert loaded.appeal_letter.appeal_id == "APL-TEST123"

    def test_by_status_and_all(self, conn):
        repo = CaseRepository(conn=conn)
        a = CaseRecord(case_id=new_case_id(), status=CaseStatus.NEW)
        b = CaseRecord(case_id=new_case_id(), status=CaseStatus.REJECTED)
        repo.create(a)
        repo.create(b)
        assert len(repo.all()) == 2
        assert len(repo.by_status(CaseStatus.REJECTED)) == 1

    def test_delete(self, conn):
        repo = CaseRepository(conn=conn)
        record = CaseRecord(case_id=new_case_id())
        repo.create(record)
        assert repo.delete(record.case_id) is True
        assert repo.get(record.case_id) is None


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #
class TestTransitions:
    def test_legal_transition(self):
        assert can_transition(CaseStatus.NEW, CaseStatus.EXTRACTED)

    def test_illegal_transition(self):
        assert not can_transition(CaseStatus.NEW, CaseStatus.APPROVED_FOR_EXPORT)

    def test_noop_transition_allowed(self):
        assert can_transition(CaseStatus.REVIEWED, CaseStatus.REVIEWED)

    def test_service_rejects_illegal_jump(self, service):
        record = service.create_case("doc.txt")
        # NEW -> REVIEWED is illegal (must go through EXTRACTED first).
        with pytest.raises(InvalidTransitionError):
            service.attach_review(record.case_id, _sample_review())


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class TestAuditRepository:
    def test_log_and_query_for_case(self, conn):
        audit = AuditRepository(conn=conn)
        audit.log("CASE-1", AuditEventType.DOCUMENT_UPLOADED, "uploaded")
        audit.log("CASE-1", AuditEventType.EXTRACTION_COMPLETED, "extracted")
        audit.log("CASE-2", AuditEventType.DOCUMENT_UPLOADED, "other")
        events = audit.for_case("CASE-1")
        assert len(events) == 2
        assert events[0].event_type is AuditEventType.DOCUMENT_UPLOADED

    def test_by_type(self, conn):
        audit = AuditRepository(conn=conn)
        audit.log("C1", AuditEventType.APPEAL_GENERATED, "x")
        audit.log("C2", AuditEventType.APPEAL_GENERATED, "y")
        audit.log("C3", AuditEventType.REVIEW_COMPLETED, "z")
        assert len(audit.by_type(AuditEventType.APPEAL_GENERATED)) == 2

    def test_actor_default_system(self, conn):
        audit = AuditRepository(conn=conn)
        ev = audit.log("C1", AuditEventType.REVIEW_COMPLETED, "x")
        assert ev.actor is AuditActor.SYSTEM


# --------------------------------------------------------------------------- #
# Full lifecycle via the service
# --------------------------------------------------------------------------- #
class TestCaseLifecycle:
    def test_happy_path_to_export(self, service):
        record = service.create_case("denial_case_06_humira.txt")
        assert record.status is CaseStatus.NEW

        service.attach_extraction(record.case_id, _sample_case())
        assert service.get_case(record.case_id).status is CaseStatus.EXTRACTED

        service.attach_review(record.case_id, _sample_review())
        assert service.get_case(record.case_id).status is CaseStatus.REVIEWED

        service.attach_appeal(record.case_id, _sample_appeal())
        # Appeal generation auto-enters the human review queue.
        assert service.get_case(record.case_id).status is CaseStatus.PENDING_HUMAN_REVIEW

        service.record_human_review(
            record.case_id, "Dr. Reviewer", HumanDecision.APPROVE, "Looks good."
        )
        final = service.get_case(record.case_id)
        assert final.status is CaseStatus.APPROVED_FOR_EXPORT
        assert final.latest_decision().decision is HumanDecision.APPROVE

        service.mark_exported(record.case_id)
        # Audit trail should include the key events.
        types = {e.event_type for e in service.history(record.case_id)}
        assert AuditEventType.DOCUMENT_UPLOADED in types
        assert AuditEventType.EXTRACTION_COMPLETED in types
        assert AuditEventType.REVIEW_COMPLETED in types
        assert AuditEventType.APPEAL_GENERATED in types
        assert AuditEventType.HUMAN_REVIEW_COMPLETED in types
        assert AuditEventType.CASE_EXPORTED in types

    def test_reject_path(self, service):
        record = service.create_case("doc.txt")
        service.attach_extraction(record.case_id, _sample_case())
        service.attach_review(record.case_id, _sample_review())
        service.attach_appeal(record.case_id, _sample_appeal())
        service.record_human_review(
            record.case_id, "Dr. Reviewer", HumanDecision.REJECT, "Insufficient."
        )
        assert service.get_case(record.case_id).status is CaseStatus.REJECTED

    def test_request_changes_returns_to_queue(self, service):
        record = service.create_case("doc.txt")
        service.attach_extraction(record.case_id, _sample_case())
        service.attach_review(record.case_id, _sample_review())
        service.attach_appeal(record.case_id, _sample_appeal())
        service.record_human_review(
            record.case_id, "Dr. Reviewer", HumanDecision.REQUEST_CHANGES, "Add detail."
        )
        assert service.get_case(record.case_id).status is CaseStatus.PENDING_HUMAN_REVIEW

    def test_assign_reviewer(self, service):
        record = service.create_case("doc.txt")
        service.assign_reviewer(record.case_id, "Nurse Adams")
        assert service.get_case(record.case_id).assigned_reviewer == "Nurse Adams"


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
class TestMetrics:
    def _full_case(self, service, decision: HumanDecision):
        record = service.create_case("doc.txt")
        service.attach_extraction(record.case_id, _sample_case())
        service.attach_review(record.case_id, _sample_review())
        service.attach_appeal(record.case_id, _sample_appeal())
        service.record_human_review(record.case_id, "Rev", decision, "c")
        return record

    def test_metrics_counts_and_rates(self, conn):
        service = CaseService(conn=conn)
        self._full_case(service, HumanDecision.APPROVE)
        self._full_case(service, HumanDecision.APPROVE)
        self._full_case(service, HumanDecision.REJECT)
        self._full_case(service, HumanDecision.REQUEST_CHANGES)

        metrics = MetricsCollector(conn=conn).collect()
        assert metrics.documents_processed == 4
        assert metrics.appeals_generated == 4
        assert metrics.human_reviews_completed == 4
        assert metrics.total_cases == 4
        # 2 approve out of 4 decided = 0.5
        assert metrics.approval_rate == 0.5
        assert metrics.rejection_rate == 0.25
        assert metrics.fallback_rate == 0.25

    def test_metrics_empty(self, conn):
        metrics = MetricsCollector(conn=conn).collect()
        assert metrics.total_cases == 0
        assert metrics.approval_rate == 0.0

    def test_average_processing_time(self, conn):
        service = CaseService(conn=conn)
        rec = service.create_case("doc.txt")
        loaded = service.get_case(rec.case_id)
        loaded.processing_seconds = 12.0
        service.cases.save(loaded)
        rec2 = service.create_case("doc2.txt")
        loaded2 = service.get_case(rec2.case_id)
        loaded2.processing_seconds = 8.0
        service.cases.save(loaded2)
        metrics = MetricsCollector(conn=conn).collect()
        assert metrics.average_processing_time == 10.0


# --------------------------------------------------------------------------- #
# Export package
# --------------------------------------------------------------------------- #
class TestExport:
    def _exported_case(self, service):
        record = service.create_case("denial_case_06_humira.txt")
        service.attach_extraction(record.case_id, _sample_case())
        service.attach_review(record.case_id, _sample_review())
        service.attach_appeal(record.case_id, _sample_appeal())
        service.record_human_review(record.case_id, "Rev", HumanDecision.APPROVE, "ok")
        return service.get_case(record.case_id)

    def test_export_files_present(self, service):
        record = self._exported_case(service)
        events = service.history(record.case_id)
        files = build_export_files(record, events)
        assert set(files) == {
            "case_summary.md",
            "patient_case.json",
            "review_result.json",
            "appeal_letter.md",
            "audit_log.json",
        }
        # JSON files parse.
        json.loads(files["patient_case.json"])
        json.loads(files["review_result.json"])
        json.loads(files["audit_log.json"])
        assert "Case Summary" in files["case_summary.md"]
        assert "## Signature" in files["appeal_letter.md"]

    def test_export_zip_contains_all(self, service):
        record = self._exported_case(service)
        events = service.history(record.case_id)
        data = build_export_zip(record, events)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert names == {
            "case_summary.md",
            "patient_case.json",
            "review_result.json",
            "appeal_letter.md",
            "audit_log.json",
        }

    def test_export_handles_missing_artifacts(self, service):
        # A NEW case with no extraction/review/appeal still exports cleanly.
        record = service.create_case("doc.txt")
        record = service.get_case(record.case_id)
        events = service.history(record.case_id)
        files = build_export_files(record, events)
        assert files["patient_case.json"] == "null"
        assert files["review_result.json"] == "null"
        assert "No appeal letter" in files["appeal_letter.md"]

"""Tests for Milestone 8: conflict resolution, authoritative facts, feedback.

All tests use an in-memory SQLite connection.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.cases.export import build_export_files, build_export_zip
from app.cases.service import CaseService
from app.feedback.dataset import FeedbackDataset
from app.models.audit_event import AuditEventType
from app.models.conflict_resolution import ResolutionSource
from app.models.reviewer_feedback import FeedbackTarget, FeedbackVerdict
from app.storage.database import connect, initialize_schema


DENIAL = (
    "Member Name: Harold Greene\nMember ID: WP-558210334\n"
    "Diagnosis: Rheumatoid arthritis\nProcedure: Humira (adalimumab)\n"
    "ICD-10: M06.9\nStatus: DENIED\n"
    "Reason for Denial: Step therapy not met; no DMARD trial documented."
)
NOTE_CONFLICT = "Patient: Harold Greene\nDiagnosis: Osteoarthritis"


@pytest.fixture
def conn():
    c = connect(":memory:")
    initialize_schema(c)
    yield c
    c.close()


@pytest.fixture
def service(conn):
    return CaseService(conn=conn)


def _case_with_conflict(service: CaseService):
    rec = service.create_case("m8 case")
    service.add_document(rec.case_id, "denial.txt", DENIAL, 1, "DENIAL_LETTER")
    service.add_document(rec.case_id, "note.txt", NOTE_CONFLICT, 1, "CLINICAL_NOTE")
    ctx = service.assemble_case(rec.case_id)
    return rec.case_id, ctx


# --------------------------------------------------------------------------- #
# Schema / backward compatibility
# --------------------------------------------------------------------------- #
class TestSchema:
    def test_new_tables_exist(self, conn):
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"conflict_resolutions", "authoritative_facts", "reviewer_feedback"} <= names

    def test_existing_tables_preserved(self, conn):
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"cases", "audit_events", "case_documents", "evidence_references"} <= names


# --------------------------------------------------------------------------- #
# System seeding
# --------------------------------------------------------------------------- #
class TestSystemFacts:
    def test_assembly_seeds_system_facts(self, service):
        case_id, ctx = _case_with_conflict(service)
        facts = service.list_authoritative_facts(case_id)
        assert facts
        assert all(
            f.resolution_source is ResolutionSource.SYSTEM for f in facts
        )

    def test_conflict_has_stable_id(self, service):
        _, ctx = _case_with_conflict(service)
        dx = [c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis"]
        assert dx and dx[0].conflict_id


# --------------------------------------------------------------------------- #
# Human conflict resolution
# --------------------------------------------------------------------------- #
class TestConflictResolution:
    def test_resolution_sets_authoritative_value(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        res, fact = service.resolve_conflict(
            case_id, conflict.conflict_id, "diagnosis",
            chosen_value="Rheumatoid arthritis",
            rejected_values=["Osteoarthritis"],
            reviewer_name="Dr. Smith",
            justification="Denial letter governs.",
            source_document="denial.txt",
            source_page=1,
        )
        assert fact.resolution_source is ResolutionSource.HUMAN
        auth = service.authoritative_facts.get(case_id, "diagnosis")
        assert auth.value == "Rheumatoid arthritis"
        assert auth.resolution_id == res.resolution_id

    def test_rejected_values_preserved(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        service.resolve_conflict(
            case_id, conflict.conflict_id, "diagnosis",
            "Rheumatoid arthritis", ["Osteoarthritis"], "Dr. Smith",
        )
        stored = service.list_resolutions(case_id)[0]
        assert "Osteoarthritis" in stored.rejected_values
        assert stored.chosen_value == "Rheumatoid arthritis"

    def test_resolution_is_append_only_history(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                 "Rheumatoid arthritis", ["Osteoarthritis"], "Dr. A")
        service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                 "Osteoarthritis", ["Rheumatoid arthritis"], "Dr. B",
                                 justification="Reversing.")
        # Both decisions retained; latest authoritative value reflects the last.
        assert service.resolutions.count_for_case(case_id) == 2
        assert service.authoritative_facts.get(case_id, "diagnosis").value == "Osteoarthritis"

    def test_requires_chosen_value(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        with pytest.raises(ValueError):
            service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                     "", ["Osteoarthritis"], "Dr. Smith")

    def test_requires_reviewer(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        with pytest.raises(ValueError):
            service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                     "Rheumatoid arthritis", [], "")


# --------------------------------------------------------------------------- #
# Case impact: review/appeal use authoritative facts
# --------------------------------------------------------------------------- #
class TestCaseImpact:
    def test_patient_case_updated_after_resolution(self, service):
        case_id, ctx = _case_with_conflict(service)
        # Auto-resolved diagnosis prefers clinical note (Osteoarthritis).
        before = service.get_case(case_id).patient_case.diagnosis
        conflict = ctx.conflict_report.conflicts[0]
        service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                 "Rheumatoid arthritis", ["Osteoarthritis"],
                                 "Dr. Smith", source_document="denial.txt", source_page=1)
        after = service.get_case(case_id).patient_case.diagnosis
        assert after == "Rheumatoid arthritis"
        assert after != before or before == "Rheumatoid arthritis"

    def test_authoritative_case_field_source_marked(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                 "Rheumatoid arthritis", ["Osteoarthritis"],
                                 "Dr. Smith", source_document="denial.txt", source_page=1)
        case = service.authoritative_patient_case(case_id)
        assert "diagnosis" in case.field_sources
        assert case.field_sources["diagnosis"].source_document == "denial.txt"

    def test_reassembly_preserves_human_fact(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                 "Rheumatoid arthritis", ["Osteoarthritis"], "Dr. Smith")
        # Re-assemble: SYSTEM seeding must NOT clobber the HUMAN decision.
        service.assemble_case(case_id)
        auth = service.authoritative_facts.get(case_id, "diagnosis")
        assert auth.resolution_source is ResolutionSource.HUMAN
        assert auth.value == "Rheumatoid arthritis"


# --------------------------------------------------------------------------- #
# Reviewer feedback
# --------------------------------------------------------------------------- #
class TestReviewerFeedback:
    def test_record_and_query(self, service):
        case_id, _ = _case_with_conflict(service)
        service.record_reviewer_feedback(
            case_id, "Dr. Smith", FeedbackTarget.EXTRACTION,
            FeedbackVerdict.PARTIAL, comments="DOB missing",
        )
        fb = service.list_feedback(case_id)
        assert len(fb) == 1
        assert fb[0].target_type is FeedbackTarget.EXTRACTION
        assert fb[0].feedback is FeedbackVerdict.PARTIAL

    def test_feedback_string_coercion(self, service):
        case_id, _ = _case_with_conflict(service)
        service.record_reviewer_feedback(case_id, "R", "appeal", "correct")
        fb = service.list_feedback(case_id)[0]
        assert fb.target_type is FeedbackTarget.APPEAL
        assert fb.feedback is FeedbackVerdict.CORRECT


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class TestAudit:
    def test_resolution_audited(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                 "Rheumatoid arthritis", ["Osteoarthritis"], "Dr. Smith")
        types = [e.event_type for e in service.history(case_id)]
        assert AuditEventType.CONFLICT_RESOLVED in types
        assert AuditEventType.AUTHORITATIVE_FACT_UPDATED in types

    def test_feedback_audited(self, service):
        case_id, _ = _case_with_conflict(service)
        service.record_reviewer_feedback(case_id, "R", "REVIEW", "INCORRECT")
        types = [e.event_type for e in service.history(case_id)]
        assert AuditEventType.REVIEWER_FEEDBACK_RECORDED in types


# --------------------------------------------------------------------------- #
# Export + learning dataset
# --------------------------------------------------------------------------- #
class TestExportAndDataset:
    def _resolved_case(self, service):
        case_id, ctx = _case_with_conflict(service)
        conflict = ctx.conflict_report.conflicts[0]
        service.resolve_conflict(case_id, conflict.conflict_id, "diagnosis",
                                 "Rheumatoid arthritis", ["Osteoarthritis"],
                                 "Dr. Smith", justification="Denial governs.")
        service.record_reviewer_feedback(case_id, "Dr. Smith", "APPEAL", "PARTIAL")
        return case_id, ctx

    def test_export_includes_m8_files(self, service):
        case_id, ctx = self._resolved_case(service)
        record = service.get_case(case_id)
        files = build_export_files(
            record,
            service.history(case_id),
            evidence=service.list_evidence(case_id),
            conflict_report=ctx.conflict_report,
            authoritative_facts=service.list_authoritative_facts(case_id),
            conflict_resolutions=service.list_resolutions(case_id),
            reviewer_feedback=service.list_feedback(case_id),
        )
        assert "authoritative_facts.json" in files
        assert "conflict_resolutions.json" in files
        assert "reviewer_feedback.json" in files
        # Authoritative diagnosis survives export.
        facts = json.loads(files["authoritative_facts.json"])
        dx = [f for f in facts if f["fact_type"] == "diagnosis"]
        assert dx and dx[0]["value"] == "Rheumatoid arthritis"
        # Rejected value preserved in resolutions export.
        res = json.loads(files["conflict_resolutions.json"])
        assert any("Osteoarthritis" in r["rejected_values"] for r in res)

    def test_export_zip_contains_m8_files(self, service):
        case_id, ctx = self._resolved_case(service)
        record = service.get_case(case_id)
        data = build_export_zip(
            record, service.history(case_id),
            evidence=service.list_evidence(case_id),
            conflict_report=ctx.conflict_report,
            authoritative_facts=service.list_authoritative_facts(case_id),
            conflict_resolutions=service.list_resolutions(case_id),
            reviewer_feedback=service.list_feedback(case_id),
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert {"authoritative_facts.json", "conflict_resolutions.json",
                "reviewer_feedback.json"} <= names

    def test_backward_compatible_export(self, service):
        # Without M8 args, only the core 5 files appear.
        rec = service.create_case("plain")
        record = service.get_case(rec.case_id)
        files = build_export_files(record, service.history(rec.case_id))
        assert set(files) == {
            "case_summary.md", "patient_case.json", "review_result.json",
            "appeal_letter.md", "audit_log.json",
        }

    def test_feedback_dataset_export(self, service):
        case_id, ctx = self._resolved_case(service)
        dataset = FeedbackDataset(
            feedback_repo=service.feedback,
            resolution_repo=service.resolutions,
            facts_repo=service.authoritative_facts,
        )
        payload = json.loads(dataset.export_json(case_id))
        assert payload["case_id"] == case_id
        assert payload["summary"]["resolution_count"] == 1
        assert payload["summary"]["feedback_count"] == 1
        assert payload["appeal_feedback"]  # APPEAL feedback captured

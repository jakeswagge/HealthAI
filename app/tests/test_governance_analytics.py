"""Tests for Milestone 11: governance, validated evidence, analytics, compliance."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.cases.export import build_export_files, build_export_zip
from app.cases.service import CaseService
from app.models.audit_event import AuditEventType
from app.models.evidence_review_decision import EvidenceDecision
from app.models.governance import EvidenceMode, GovernanceSettings
from app.storage.database import connect, initialize_schema


DENIAL = (
    "Member Name: Harold Greene\nMember ID: WP-558210334\n"
    "Diagnosis: Rheumatoid arthritis\nProcedure: Humira (adalimumab)\n"
    "ICD-10: M06.9\nStatus: DENIED\n"
    "Reason for Denial: Step therapy not met; no DMARD trial documented."
)
NOTE = "Patient: Harold Greene\nDiagnosis: Osteoarthritis"
LAB = "Member ID: WP-558210334\nRheumatoid Factor: 85 HIGH"


@pytest.fixture
def conn():
    c = connect(":memory:")
    initialize_schema(c)
    yield c
    c.close()


@pytest.fixture
def service(conn):
    return CaseService(conn=conn)


def _scored_case(service, with_decisions=True):
    rec = service.create_case("gov case")
    service.ingest_document(rec.case_id, "denial.png", DENIAL.encode())
    service.ingest_document(rec.case_id, "note.png", NOTE.encode())
    service.assemble_case(rec.case_id)
    service.score_evidence(rec.case_id)
    return rec.case_id


# --------------------------------------------------------------------------- #
# Schema + settings
# --------------------------------------------------------------------------- #
class TestSchema:
    def test_governance_table_exists(self, conn):
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "governance_settings" in names

    def test_existing_tables_preserved(self, conn):
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"cases", "evidence_references", "evidence_quality",
                "evidence_review_decisions"} <= names


class TestGovernanceSettings:
    def test_defaults_are_draft(self, service):
        s = service.get_governance_settings()
        assert s.validated_evidence_mode is False
        assert s.mode is EvidenceMode.DRAFT

    def test_update_and_persist(self, service):
        service.update_governance_settings(
            GovernanceSettings(validated_evidence_mode=True, minimum_quality_score=0.5)
        )
        s = service.get_governance_settings()
        assert s.validated_evidence_mode is True
        assert s.minimum_quality_score == 0.5
        assert s.mode is EvidenceMode.VALIDATED

    def test_update_audited(self, service):
        service.update_governance_settings(GovernanceSettings(validated_evidence_mode=True))
        events = service.audit.by_type(AuditEventType.GOVERNANCE_SETTINGS_UPDATED)
        assert events

    def test_min_quality_clamped(self):
        assert GovernanceSettings(minimum_quality_score=5).minimum_quality_score == 1.0
        assert GovernanceSettings(minimum_quality_score=-1).minimum_quality_score == 0.0


# --------------------------------------------------------------------------- #
# Validated evidence engine
# --------------------------------------------------------------------------- #
class TestValidatedEvidenceMode:
    def test_draft_mode_includes_all(self, service):
        case_id = _scored_case(service)
        # default settings = draft
        used, aset = service.evidence_for_consumption(case_id)
        assert aset.mode is EvidenceMode.DRAFT
        assert aset.included_count == len(service.list_evidence(case_id))
        assert aset.excluded_count == 0

    def test_validated_excludes_rejected(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", EvidenceDecision.REJECT)
        service.update_governance_settings(GovernanceSettings(validated_evidence_mode=True))
        used, aset = service.evidence_for_consumption(case_id)
        assert aset.mode is EvidenceMode.VALIDATED
        assert evs[0].evidence_id not in set(aset.included_ids)
        assert any(x.evidence_id == evs[0].evidence_id for x in aset.excluded)

    def test_rejected_never_in_validated_even_if_high_quality(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        # Reject a piece of evidence then require nothing else.
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "REJECT")
        service.update_governance_settings(
            GovernanceSettings(validated_evidence_mode=True, allow_unreviewed_evidence=True)
        )
        used, aset = service.evidence_for_consumption(case_id)
        assert evs[0].evidence_id not in {e.evidence_id for e in used}

    def test_validated_requires_approval_when_configured(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "APPROVE")
        service.update_governance_settings(
            GovernanceSettings(validated_evidence_mode=True, allow_unreviewed_evidence=False)
        )
        used, aset = service.evidence_for_consumption(case_id)
        # Only the explicitly approved evidence is included.
        assert set(aset.included_ids) == {evs[0].evidence_id}

    def test_minimum_quality_threshold_excludes_low(self, service):
        case_id = _scored_case(service)
        # Set an impossibly high threshold -> everything excluded.
        service.update_governance_settings(
            GovernanceSettings(validated_evidence_mode=True, minimum_quality_score=1.01 if False else 1.0,
                               allow_unreviewed_evidence=True)
        )
        used, aset = service.evidence_for_consumption(case_id)
        # With threshold 1.0, references scoring < 1.0 are excluded.
        assert aset.excluded_count >= 1

    def test_validated_application_audited(self, service):
        case_id = _scored_case(service)
        service.update_governance_settings(GovernanceSettings(validated_evidence_mode=True))
        service.evidence_for_consumption(case_id)
        events = [e.event_type for e in service.history(case_id)]
        assert AuditEventType.VALIDATED_EVIDENCE_APPLIED in events


# --------------------------------------------------------------------------- #
# Review / appeal integration (draft vs validated)
# --------------------------------------------------------------------------- #
class TestReviewAppealIntegration:
    def test_draft_vs_validated_evidence_differs(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "REJECT")

        # Draft mode: all evidence.
        draft_used, _ = service.evidence_for_consumption(case_id, GovernanceSettings())
        # Validated mode: rejected excluded.
        validated_used, _ = service.evidence_for_consumption(
            case_id, GovernanceSettings(validated_evidence_mode=True)
        )
        assert len(validated_used) < len(draft_used)


# --------------------------------------------------------------------------- #
# Compliance
# --------------------------------------------------------------------------- #
class TestCompliance:
    def test_unresolved_conflicts_flagged(self, service):
        case_id = _scored_case(service)  # denial vs note -> diagnosis conflict
        report = service.check_compliance(
            case_id, GovernanceSettings(require_conflict_resolution=True)
        )
        assert not report.is_compliant
        assert any(v.code == "UNRESOLVED_CONFLICTS" for v in report.violations)

    def test_export_without_human_review_flagged(self, service):
        case_id = _scored_case(service)
        service.update_governance_settings(
            GovernanceSettings(
                confidence_threshold=0.0,
                require_human_review_before_export=False,
            )
        )
        service.mark_exported(case_id)
        report = service.check_compliance(
            case_id,
            GovernanceSettings(
                require_human_review_before_export=True,
                confidence_threshold=0.0,
            ),
        )
        assert any(v.code == "EXPORT_WITHOUT_HUMAN_REVIEW" for v in report.violations)

    def test_low_quality_evidence_flagged(self, service):
        case_id = _scored_case(service)
        report = service.check_compliance(
            case_id, GovernanceSettings(minimum_quality_score=1.0)
        )
        assert any(v.code == "LOW_QUALITY_EVIDENCE_PRESENT" for v in report.violations)

    def test_compliant_case_passes(self, service):
        case_id = _scored_case(service)
        # Resolve the diagnosis conflict so require_conflict_resolution passes.
        ctx = service.assemble_case(case_id)
        dx = next(c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis")
        service.resolve_conflict(case_id, dx.conflict_id, "diagnosis",
                                 "Rheumatoid arthritis", ["Osteoarthritis"], "Rev")
        report = service.check_compliance(
            case_id,
            GovernanceSettings(
                require_conflict_resolution=True,
                confidence_threshold=0.0,
            ),
        )
        # No appeal, no export, conflict resolved, no min-quality -> compliant.
        assert report.is_compliant

    def test_compliance_audited(self, service):
        case_id = _scored_case(service)
        service.check_compliance(case_id)
        types = [e.event_type for e in service.history(case_id)]
        assert AuditEventType.COMPLIANCE_CHECK_RUN in types


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
class TestAnalytics:
    def test_analytics_rates(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "APPROVE")
        service.record_evidence_decision(case_id, evs[1].evidence_id, "Rev", "REJECT")
        service.record_evidence_decision(case_id, evs[2].evidence_id, "Rev", "FLAG")
        a = service.quality_analytics()
        assert a.evidence_decisions == 3
        assert a.evidence_approval_rate == pytest.approx(1 / 3, abs=0.01)
        assert a.evidence_rejection_rate == pytest.approx(1 / 3, abs=0.01)
        assert a.evidence_flag_rate == pytest.approx(1 / 3, abs=0.01)
        assert 0.0 <= a.average_quality_score <= 1.0

    def test_conflict_rate(self, service):
        _scored_case(service)  # has a diagnosis conflict
        a = service.quality_analytics()
        assert a.conflict_rate > 0.0

    def test_analytics_empty(self, service):
        a = service.quality_analytics()
        assert a.total_cases == 0
        assert a.evidence_approval_rate == 0.0

    def test_analytics_serializable(self, service):
        _scored_case(service)
        d = service.quality_analytics().as_dict()
        assert "average_quality_score" in d
        assert "appeal_generation_success_rate" in d


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
class TestExport:
    def _prepared(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "REJECT")
        service.update_governance_settings(GovernanceSettings(validated_evidence_mode=True))
        return case_id

    def test_export_includes_m11_files(self, service):
        case_id = self._prepared(service)
        used, aset = service.evidence_for_consumption(case_id)
        report = service.check_compliance(case_id)
        files = build_export_files(
            service.get_case(case_id), service.history(case_id),
            all_evidence=service.list_evidence(case_id),
            approved_evidence_set=aset,
            governance_report=report,
            quality_analytics=service.quality_analytics().as_dict(),
        )
        assert "governance_report.json" in files
        assert "quality_analytics.json" in files
        assert "approved_evidence.json" in files
        assert "excluded_evidence.json" in files
        excluded = json.loads(files["excluded_evidence.json"])
        assert excluded["excluded_count"] >= 1

    def test_export_zip_contains_m11_files(self, service):
        case_id = self._prepared(service)
        used, aset = service.evidence_for_consumption(case_id)
        report = service.check_compliance(case_id)
        data = build_export_zip(
            service.get_case(case_id), service.history(case_id),
            all_evidence=service.list_evidence(case_id),
            approved_evidence_set=aset,
            governance_report=report,
            quality_analytics=service.quality_analytics().as_dict(),
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert {"governance_report.json", "quality_analytics.json",
                "approved_evidence.json", "excluded_evidence.json"} <= names

    def test_backward_compatible_export(self, service):
        rec = service.create_case("plain")
        files = build_export_files(service.get_case(rec.case_id), service.history(rec.case_id))
        assert "governance_report.json" not in files
        assert set(files) == {
            "case_summary.md", "patient_case.json", "review_result.json",
            "appeal_letter.md", "audit_log.json",
        }


# --------------------------------------------------------------------------- #
# Success criterion
# --------------------------------------------------------------------------- #
class TestSuccessCriterion:
    def test_draft_and_validated_modes_differ(self, service):
        rec = service.create_case("m11 success")
        service.ingest_document(rec.case_id, "denial.png", DENIAL.encode())
        service.ingest_document(rec.case_id, "note.png", NOTE.encode())
        service.ingest_document(rec.case_id, "lab.png", LAB.encode())
        service.assemble_case(rec.case_id)
        service.score_evidence(rec.case_id)
        evs = service.list_evidence(rec.case_id)
        # approved / rejected / flagged evidence present.
        service.record_evidence_decision(rec.case_id, evs[0].evidence_id, "Rev", "APPROVE")
        service.record_evidence_decision(rec.case_id, evs[1].evidence_id, "Rev", "REJECT")
        service.record_evidence_decision(rec.case_id, evs[2].evidence_id, "Rev", "FLAG")

        draft_used, _ = service.evidence_for_consumption(rec.case_id, GovernanceSettings())
        validated_used, vset = service.evidence_for_consumption(
            rec.case_id, GovernanceSettings(validated_evidence_mode=True)
        )
        assert len(draft_used) == len(evs)
        assert len(validated_used) == len(evs) - 1  # rejected excluded
        # Governance report + analytics produced.
        report = service.check_compliance(rec.case_id, GovernanceSettings(validated_evidence_mode=True))
        assert report.case_id == rec.case_id
        assert service.quality_analytics().total_cases == 1

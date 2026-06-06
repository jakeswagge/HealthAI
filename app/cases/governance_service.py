"""GovernanceService: governance settings, validated evidence, compliance.

Extracted from ``CaseService`` during the Milestone 12 facade decomposition
(Milestone 11 functionality). Owns the governance concern: org-wide settings,
building the governance-filtered :class:`ApprovedEvidenceSet`, choosing the
evidence downstream consumers should use, and running compliance checks.

Behavior is identical to the original CaseService methods - this is a cohesion
extraction, not a logic change.
"""

from __future__ import annotations

from app.assembly.engine import CaseAssemblyEngine
from app.audit.repository import AuditRepository
from app.cases.document_repository import CaseDocumentRepository
from app.cases.lifecycle import CaseLifecycle
from app.evidence.repository import EvidenceRepository
from app.governance.compliance import GovernanceComplianceChecker
from app.governance.engine import ValidatedEvidenceEngine
from app.governance.repository import GovernanceSettingsRepository
from app.quality.repository import EvidenceQualityRepository
from app.quality.workbench import ReviewerWorkbench
from app.resolution.repository import ConflictResolutionRepository
from app.models.audit_event import AuditActor, AuditEventType
from app.models.evidence_reference import EvidenceReference
from app.models.governance import (
    ApprovedEvidenceSet,
    GovernanceComplianceReport,
    GovernanceSettings,
)


class GovernanceService:
    """Govern which evidence downstream review/appeal may use + compliance."""

    def __init__(
        self,
        lifecycle: CaseLifecycle,
        documents: CaseDocumentRepository,
        evidence: EvidenceRepository,
        evidence_quality: EvidenceQualityRepository,
        assembly: CaseAssemblyEngine,
        workbench: ReviewerWorkbench,
        governance_settings: GovernanceSettingsRepository,
        validated_evidence: ValidatedEvidenceEngine,
        compliance_checker: GovernanceComplianceChecker,
        resolutions: ConflictResolutionRepository,
        audit: AuditRepository,
    ) -> None:
        self.lifecycle = lifecycle
        self.documents = documents
        self.evidence = evidence
        self.evidence_quality = evidence_quality
        self.assembly = assembly
        self.workbench = workbench
        self.governance_settings = governance_settings
        self.validated_evidence = validated_evidence
        self.compliance_checker = compliance_checker
        self.resolutions = resolutions
        self.audit = audit

    def get_governance_settings(self) -> GovernanceSettings:
        return self.governance_settings.get()

    def update_governance_settings(
        self, settings: GovernanceSettings, actor: str = "admin"
    ) -> GovernanceSettings:
        """Persist governance settings and audit the change (global event)."""
        saved = self.governance_settings.save(settings)
        # Governance is org-wide; record against a sentinel case id so the
        # change is auditable without being tied to one case.
        self.audit.log(
            "GOVERNANCE",
            AuditEventType.GOVERNANCE_SETTINGS_UPDATED,
            details=(
                f"{actor} updated governance: validated_mode="
                f"{saved.validated_evidence_mode}, "
                f"allow_unreviewed={saved.allow_unreviewed_evidence}, "
                f"min_quality={saved.minimum_quality_score:.2f}, "
                f"require_conflict_resolution={saved.require_conflict_resolution}, "
                f"require_human_review_before_export="
                f"{saved.require_human_review_before_export}."
            ),
            actor=AuditActor.USER,
        )
        return saved

    def build_approved_evidence_set(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> ApprovedEvidenceSet:
        """Apply governance settings to a case's evidence (audited)."""
        self.lifecycle.require(case_id)
        settings = settings or self.get_governance_settings()
        evidence = self.evidence.for_case(case_id)
        approved_ids = self.workbench.approved_evidence_ids(case_id)
        rejected_ids = self.workbench.rejected_evidence_ids(case_id)
        quality_by_id = {
            q.evidence_id: q for q in self.evidence_quality.for_case(case_id)
        }
        result = self.validated_evidence.build_set(
            case_id,
            evidence,
            settings,
            approved_ids=approved_ids,
            rejected_ids=rejected_ids,
            quality_by_id=quality_by_id,
        )
        self.audit.log(
            case_id,
            AuditEventType.VALIDATED_EVIDENCE_APPLIED,
            details=(
                f"Mode={result.mode.value}: {result.included_count} included, "
                f"{result.excluded_count} excluded."
            ),
        )
        return result

    def evidence_for_consumption(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> tuple[list[EvidenceReference], ApprovedEvidenceSet]:
        """Return the evidence downstream consumers should use + the approved set.

        Draft mode -> all evidence. Validated mode -> governance-filtered set.
        Rejected evidence is never returned in validated mode.
        """
        approved_set = self.build_approved_evidence_set(case_id, settings)
        evidence = self.evidence.for_case(case_id)
        filtered = self.validated_evidence.filter_evidence(evidence, approved_set)
        return filtered, approved_set

    def unresolved_conflicts(self, case_id: str) -> list[str]:
        """Fact types with a detected conflict that lacks a resolution."""
        docs = self.documents.for_case(case_id)
        if not docs:
            return []
        report = self.assembly.assemble(case_id, docs).conflict_report
        if not report.has_conflicts:
            return []
        resolved_fact_types = {r.fact_type for r in self.resolutions.for_case(case_id)}
        return [
            c.fact_type for c in report.conflicts
            if c.fact_type not in resolved_fact_types
        ]

    def check_compliance(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> GovernanceComplianceReport:
        """Run a governance compliance check on a case (audited)."""
        record = self.lifecycle.require(case_id)
        settings = settings or self.get_governance_settings()
        quality = self.evidence_quality.for_case(case_id)
        docs = self.documents.for_case(case_id)
        conflict_report = (
            self.assembly.assemble(case_id, docs).conflict_report if docs else None
        )
        was_exported = any(
            e.event_type is AuditEventType.CASE_EXPORTED
            for e in self.audit.for_case(case_id)
        )
        used_ids = {
            e.evidence_id for e in self.evidence_for_consumption(case_id, settings)[0]
        }

        report = self.compliance_checker.check(
            case_id,
            settings,
            has_appeal=record.appeal_letter is not None,
            has_human_review=bool(record.review_decisions),
            was_exported=was_exported,
            quality=quality,
            conflict_report=conflict_report,
            unresolved_conflict_fact_types=self.unresolved_conflicts(case_id),
            used_evidence_ids=used_ids,
        )
        self.audit.log(
            case_id,
            AuditEventType.COMPLIANCE_CHECK_RUN,
            details=(
                f"Compliance: {'PASS' if report.is_compliant else 'FAIL'}; "
                f"{len(report.violations)} violation(s)."
            ),
        )
        return report

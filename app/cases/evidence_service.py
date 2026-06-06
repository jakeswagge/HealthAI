"""EvidenceService: assembly, evidence inventory, quality scoring, workbench.

Extracted from ``CaseService`` during the Milestone 12 facade decomposition.
Owns the evidence concern end-to-end: assembling documents into a unified,
evidence-backed context (Milestone 6/7), scoring evidence quality (Milestone
10), the reviewer workbench views + decisions (Milestone 10), and the
"approved evidence" gate that respects reviewer authority.

Behavior is identical to the original CaseService methods - this is a cohesion
extraction, not a logic change.
"""

from __future__ import annotations

from app.assembly.engine import CaseAssemblyEngine
from app.audit.repository import AuditRepository
from app.cases.document_repository import CaseDocumentRepository
from app.cases.lifecycle import CaseLifecycle
from app.evidence.repository import EvidenceRepository
from app.quality.decision_repository import EvidenceReviewDecisionRepository
from app.quality.engine import EvidenceQualityEngine
from app.quality.repository import EvidenceQualityRepository
from app.quality.workbench import EvidenceView, ReviewerWorkbench
from app.resolution.engine import ConflictResolutionEngine
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_record import CaseStatus
from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.evidence_reference import EvidenceReference
from app.models.evidence_review_decision import (
    EvidenceDecision,
    EvidenceReviewDecision,
)
from app.models.unified_case_context import UnifiedCaseContext


class EvidenceService:
    """Assemble, score, and review the evidence inventory for cases."""

    def __init__(
        self,
        lifecycle: CaseLifecycle,
        documents: CaseDocumentRepository,
        evidence: EvidenceRepository,
        assembly: CaseAssemblyEngine,
        resolution_engine: ConflictResolutionEngine,
        evidence_quality: EvidenceQualityRepository,
        evidence_decisions: EvidenceReviewDecisionRepository,
        quality_engine: EvidenceQualityEngine,
        workbench: ReviewerWorkbench,
        audit: AuditRepository,
    ) -> None:
        self.lifecycle = lifecycle
        self.documents = documents
        self.evidence = evidence
        self.assembly = assembly
        self.resolution_engine = resolution_engine
        self.evidence_quality = evidence_quality
        self.evidence_decisions = evidence_decisions
        self.quality_engine = quality_engine
        self.workbench = workbench
        self.audit = audit

    # ------------------------------------------------------------------ #
    # Multi-document assembly (Milestone 6/7)
    # ------------------------------------------------------------------ #
    def assemble_case(self, case_id: str) -> UnifiedCaseContext:
        """Assemble all of a case's documents into a UnifiedCaseContext.

        Persists the evidence inventory, attaches the synthesized PatientCase
        (moving NEW -> EXTRACTED), and records audit events. The full context
        (conflicts, missing info) is returned for the caller/UI.
        """
        record = self.lifecycle.require(case_id)
        documents = self.documents.for_case(case_id)
        context = self.assembly.assemble(case_id, documents)

        # Persist evidence (replace so re-assembly is idempotent).
        self.evidence.replace_for_case(case_id, context.evidence)
        # Seed SYSTEM authoritative facts (never overrides HUMAN resolutions).
        self.resolution_engine.seed_system_facts(context)
        self.audit.log(
            case_id,
            AuditEventType.EXTRACTION_COMPLETED,
            details=(
                f"Assembled {len(documents)} document(s); "
                f"{len(context.evidence)} evidence reference(s); "
                f"{len(context.conflict_report.conflicts)} conflict(s)."
            ),
        )

        # Attach the synthesized case, with any HUMAN authoritative facts applied
        # (advances status if still NEW).
        record.patient_case = self.resolution_engine.apply_to_case(
            context.patient_case, case_id
        )
        if record.status == CaseStatus.NEW:
            self.lifecycle.set_status(record, CaseStatus.EXTRACTED)
        self.lifecycle.save(record)
        return context

    def list_evidence(self, case_id: str):
        return self.evidence.for_case(case_id)

    # ------------------------------------------------------------------ #
    # Evidence quality + reviewer workbench (Milestone 10)
    # ------------------------------------------------------------------ #
    def score_evidence(self, case_id: str) -> list[EvidenceQualityAssessment]:
        """Score every evidence reference for a case and persist the results."""
        self.lifecycle.require(case_id)
        evidence = self.evidence.for_case(case_id)
        assessments = self.quality_engine.assess_all(evidence, case_id=case_id)
        self.evidence_quality.replace_for_case(case_id, assessments)
        weak = sum(1 for a in assessments if a.is_weak)
        self.audit.log(
            case_id,
            AuditEventType.EVIDENCE_QUALITY_SCORED,
            details=(
                f"Scored {len(assessments)} evidence reference(s); "
                f"{weak} flagged as weak."
            ),
        )
        return assessments

    def list_evidence_quality(self, case_id: str) -> list[EvidenceQualityAssessment]:
        return self.evidence_quality.for_case(case_id)

    def build_evidence_views(self, case_id: str) -> list[EvidenceView]:
        """Build reviewer-workbench views (evidence + quality + decisions)."""
        evidence = self.evidence.for_case(case_id)
        return self.workbench.build_views(evidence)

    def record_evidence_decision(
        self,
        case_id: str,
        evidence_id: str,
        reviewer: str,
        decision: EvidenceDecision | str,
        comments: str = "",
    ) -> EvidenceReviewDecision:
        """Record a reviewer APPROVE/REJECT/FLAG decision on a piece of evidence."""
        self.lifecycle.require(case_id)
        d = self.workbench.record_decision(
            evidence_id=evidence_id,
            case_id=case_id,
            reviewer=reviewer,
            decision=decision,
            comments=comments,
        )
        self.audit.log(
            case_id,
            AuditEventType.EVIDENCE_REVIEW_DECISION,
            details=(
                f"{d.reviewer} marked evidence {evidence_id} as {d.decision.value}. "
                f"{comments}"
            ).strip(),
            actor=AuditActor.USER,
        )
        return d

    def list_evidence_decisions(self, case_id: str) -> list[EvidenceReviewDecision]:
        return self.evidence_decisions.for_case(case_id)

    def approved_evidence(self, case_id: str) -> list[EvidenceReference]:
        """Evidence usable downstream: not rejected (approved or undecided).

        Once a reviewer has begun validating (any decision exists), rejected
        evidence is excluded; approved + still-pending evidence remains usable.
        This preserves reviewer authority without discarding unreviewed facts.
        """
        evidence = self.evidence.for_case(case_id)
        rejected = self.workbench.rejected_evidence_ids(case_id)
        return [e for e in evidence if e.evidence_id not in rejected]

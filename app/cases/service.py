"""CaseService: facade over cohesive case sub-services + audit logging.

This is the single entry point the UI (and tests) use to drive the workflow.
It does NOT run extraction/review/appeal agents itself - callers pass in the
already-produced artifacts. That keeps case management independent of those
engines while still recording the workflow and its audit trail.

Architecture (Milestone 12)
---------------------------
``CaseService`` used to be a ~700-line god object with ~37 public methods. It is
now a thin **facade** that:

1. owns the shared SQLite connection and constructs every repository/engine as a
   public attribute (preserving the long-standing ``service.<repo>`` access
   pattern that the UI and tests rely on), and
2. delegates each public method to one of the cohesive sub-services:

   - :class:`~app.cases.ingestion_service.IngestionService`  - documents + OCR
   - :class:`~app.cases.evidence_service.EvidenceService`    - assembly + quality
   - :class:`~app.cases.review_service.ReviewService`        - extraction/review
   - :class:`~app.cases.appeal_service.AppealService`        - appeals + export mark
   - :class:`~app.cases.resolution_service.ResolutionService`- conflicts + feedback
   - :class:`~app.cases.governance_service.GovernanceService`- governance + compliance
   - :class:`~app.cases.analytics_service.AnalyticsService`  - quality analytics
   - :class:`~app.cases.export_service.ExportService`        - export packages

The public method + attribute surface is unchanged; behavior is identical. Every
significant action still records an :class:`AuditEvent` (now via the relevant
sub-service, which shares the same repositories).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from app.audit.repository import AuditRepository
from app.cases.repository import CaseRepository, new_case_id
from app.cases.document_repository import CaseDocumentRepository
from app.cases.lifecycle import CaseLifecycle
from app.assembly.engine import CaseAssemblyEngine
from app.evidence.repository import EvidenceRepository
from app.ingestion.engine import DocumentIngestionEngine, IngestionResult
from app.ocr.repository import OCRResultRepository
from app.ocr.providers import get_ocr_provider
from app.vision.extractor import VisionEvidenceExtractor
from app.quality.engine import EvidenceQualityEngine
from app.quality.repository import EvidenceQualityRepository
from app.quality.decision_repository import EvidenceReviewDecisionRepository
from app.quality.workbench import ReviewerWorkbench, EvidenceView
from app.governance.repository import GovernanceSettingsRepository
from app.governance.engine import ValidatedEvidenceEngine
from app.governance.compliance import GovernanceComplianceChecker
from app.analytics.quality_analytics import QualityAnalytics, QualityAnalyticsEngine
from app.resolution.repository import (
    AuthoritativeFactRepository,
    ConflictResolutionRepository,
)
from app.resolution.engine import ConflictResolutionEngine
from app.feedback.repository import ReviewerFeedbackRepository
from app.explainability.engine import ExplainabilityEngine

# Cohesive sub-services (Milestone 12 decomposition).
from app.cases.analytics_service import AnalyticsService
from app.cases.appeal_service import AppealService
from app.cases.evidence_service import EvidenceService
from app.cases.explainability_service import (
    ExplainabilityService,
    GovernedAppeal,
    GovernedReview,
)
from app.cases.export_service import ExportService
from app.cases.governance_service import GovernanceService
from app.cases.ingestion_service import DocumentOCRStatus, IngestionService
from app.cases.payer_service import PayerAppeal, PayerReview, PayerService
from app.cases.resolution_service import ResolutionService
from app.cases.review_service import ReviewService
from app.operations.health import OperationalHealthMonitor

from app.models.appeal_letter import AppealLetter
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.case_record import CaseRecord, HumanDecision
from app.models.conflict_resolution import AuthoritativeFact, ConflictResolution
from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.evidence_reference import EvidenceReference
from app.models.evidence_review_decision import (
    EvidenceDecision,
    EvidenceReviewDecision,
)
from app.models.explanation import (
    AppealExplanation,
    ReviewExplanation,
    TraceabilityChain,
)
from app.models.governance import (
    ApprovedEvidenceSet,
    GovernanceComplianceReport,
    GovernanceSettings,
)
from app.models.safety import SafetyGateDecision
from app.models.ocr_result import DEFAULT_OCR_CONFIDENCE_THRESHOLD, OCRPageResult
from app.models.operational_health import OperationalHealthReport
from app.models.patient_case import PatientCase
from app.models.payer import PayerProfile
from app.models.review_result import ReviewResult
from app.models.reviewer_feedback import (
    FeedbackTarget,
    FeedbackVerdict,
    ReviewerFeedback,
)
from app.models.unified_case_context import UnifiedCaseContext
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class CaseService:
    """High-level workflow operations over cases + audit (facade)."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        # Share one connection between all repositories so a single in-memory
        # DB works for tests and a single file is used in production.
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

        # --- Repositories + engines (public attributes; unchanged surface). ---
        self.cases = CaseRepository(conn=self.conn)
        self.audit = AuditRepository(conn=self.conn)
        self.documents = CaseDocumentRepository(conn=self.conn)
        self.evidence = EvidenceRepository(conn=self.conn)
        self.assembly = CaseAssemblyEngine()
        # Milestone 8: conflict resolution + feedback.
        self.resolutions = ConflictResolutionRepository(conn=self.conn)
        self.authoritative_facts = AuthoritativeFactRepository(conn=self.conn)
        self.feedback = ReviewerFeedbackRepository(conn=self.conn)
        self.resolution_engine = ConflictResolutionEngine(
            resolutions=self.resolutions,
            facts=self.authoritative_facts,
            audit=self.audit,
        )
        # Milestone 9: OCR + intelligent ingestion + vision evidence.
        self.ocr_results = OCRResultRepository(conn=self.conn)
        self.ingestion = DocumentIngestionEngine(ocr_provider=get_ocr_provider())
        self.vision = VisionEvidenceExtractor()
        # Milestone 10: Claude evidence extraction + quality + workbench.
        self.evidence_quality = EvidenceQualityRepository(conn=self.conn)
        self.evidence_decisions = EvidenceReviewDecisionRepository(conn=self.conn)
        self.quality_engine = EvidenceQualityEngine()
        self.workbench = ReviewerWorkbench(
            quality_repo=self.evidence_quality,
            decision_repo=self.evidence_decisions,
        )
        # Milestone 11: governance + validated evidence + analytics.
        self.governance_settings = GovernanceSettingsRepository(conn=self.conn)
        self.validated_evidence = ValidatedEvidenceEngine()
        self.compliance_checker = GovernanceComplianceChecker()
        self.analytics = QualityAnalyticsEngine(
            conn=self.conn,
            case_repository=self.cases,
            document_repository=self.documents,
        )
        # Milestone 13: governance-enforced reviews/appeals + explainability.
        self.explainability_engine = ExplainabilityEngine()

        # --- Shared lifecycle + cohesive sub-services (Milestone 12). ---
        self._lifecycle = CaseLifecycle(cases=self.cases, audit=self.audit)
        self._ingestion = IngestionService(
            lifecycle=self._lifecycle,
            documents=self.documents,
            ocr_results=self.ocr_results,
            ingestion=self.ingestion,
            audit=self.audit,
        )
        self._evidence = EvidenceService(
            lifecycle=self._lifecycle,
            documents=self.documents,
            evidence=self.evidence,
            assembly=self.assembly,
            resolution_engine=self.resolution_engine,
            evidence_quality=self.evidence_quality,
            evidence_decisions=self.evidence_decisions,
            quality_engine=self.quality_engine,
            workbench=self.workbench,
            audit=self.audit,
        )
        self._review = ReviewService(
            lifecycle=self._lifecycle,
            audit=self.audit,
            settings_provider=self.get_governance_settings,
            evidence_repository=self.evidence,
            workbench=self.workbench,
        )
        self._appeal = AppealService(
            lifecycle=self._lifecycle,
            audit=self.audit,
            settings_provider=self.get_governance_settings,
        )
        self._resolution = ResolutionService(
            lifecycle=self._lifecycle,
            resolution_engine=self.resolution_engine,
            resolutions=self.resolutions,
            authoritative_facts=self.authoritative_facts,
            feedback=self.feedback,
            audit=self.audit,
        )
        self._governance = GovernanceService(
            lifecycle=self._lifecycle,
            documents=self.documents,
            evidence=self.evidence,
            evidence_quality=self.evidence_quality,
            assembly=self.assembly,
            workbench=self.workbench,
            governance_settings=self.governance_settings,
            validated_evidence=self.validated_evidence,
            compliance_checker=self.compliance_checker,
            resolutions=self.resolutions,
            audit=self.audit,
        )
        self._analytics = AnalyticsService(analytics=self.analytics)
        self._export = ExportService()
        self._explainability = ExplainabilityService(
            lifecycle=self._lifecycle,
            documents=self.documents,
            evidence=self.evidence,
            evidence_quality=self.evidence_quality,
            evidence_decisions=self.evidence_decisions,
            assembly=self.assembly,
            governance=self._governance,
            audit=self.audit,
            explainability=self.explainability_engine,
        )
        # Final Milestone: payer guideline packs + operational health.
        self._payer = PayerService(
            lifecycle=self._lifecycle,
            explainability=self._explainability,
        )
        self._health = OperationalHealthMonitor(
            conn=self.conn,
            case_repository=self.cases,
            document_repository=self.documents,
            assembly=self.assembly,
            compliance_fn=self.check_compliance,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def create_case(self, source_filename: Optional[str] = None) -> CaseRecord:
        """Create a NEW case and record DOCUMENT_UPLOADED + creation."""
        record = CaseRecord(case_id=new_case_id(), source_filename=source_filename)
        self.cases.create(record)
        self.audit.log(
            record.case_id,
            AuditEventType.CASE_CREATED,
            details=f"Case created for '{source_filename or 'unknown source'}'.",
        )
        self.audit.log(
            record.case_id,
            AuditEventType.DOCUMENT_UPLOADED,
            details=f"Document uploaded: {source_filename or 'unknown'}.",
            actor=AuditActor.USER,
        )
        return record

    def attach_extraction(
        self, case_id: str, patient_case: PatientCase
    ) -> CaseRecord:
        """Attach extraction output and move to EXTRACTED."""
        return self._review.attach_extraction(case_id, patient_case)

    # ------------------------------------------------------------------ #
    # Documents + intelligent ingestion + OCR (Milestone 6/7 + 9)
    # ------------------------------------------------------------------ #
    def add_document(
        self,
        case_id: str,
        filename: str,
        raw_text: str,
        page_count: int = 1,
        document_type: DocumentCategory | str | None = None,
    ) -> CaseDocument:
        """Attach a supporting document to a case and record an audit event."""
        return self._ingestion.add_document(
            case_id, filename, raw_text, page_count, document_type
        )

    def list_documents(self, case_id: str) -> list[CaseDocument]:
        return self._ingestion.list_documents(case_id)

    def ingest_document(
        self,
        case_id: str,
        filename: str,
        data: bytes,
        category_override: DocumentCategory | str | None = None,
        ocr_confidence_threshold: float = DEFAULT_OCR_CONFIDENCE_THRESHOLD,
    ) -> tuple[CaseDocument, IngestionResult]:
        """Ingest any supported upload (TXT/PDF/PNG/JPG/JPEG) into a case."""
        return self._ingestion.ingest_document(
            case_id,
            filename,
            data,
            category_override=category_override,
            ocr_confidence_threshold=ocr_confidence_threshold,
        )

    def list_ocr_results(self, case_id: str) -> list[OCRPageResult]:
        return self._ingestion.list_ocr_results(case_id)

    def ocr_results_for_document(self, document_id: str) -> list[OCRPageResult]:
        return self._ingestion.ocr_results_for_document(document_id)

    def describe_ocr(self) -> str:
        return self._ingestion.describe_ocr()

    def ocr_readiness(self):
        return self._ingestion.ocr_readiness()

    def document_ocr_statuses(self, case_id: str) -> list[DocumentOCRStatus]:
        return self._ingestion.document_ocr_statuses(case_id)

    # ------------------------------------------------------------------ #
    # Assembly + evidence quality + reviewer workbench (Milestone 6/7 + 10)
    # ------------------------------------------------------------------ #
    def assemble_case(self, case_id: str) -> UnifiedCaseContext:
        """Assemble all of a case's documents into a UnifiedCaseContext."""
        return self._evidence.assemble_case(case_id)

    def list_evidence(self, case_id: str):
        return self._evidence.list_evidence(case_id)

    def score_evidence(self, case_id: str) -> list[EvidenceQualityAssessment]:
        """Score every evidence reference for a case and persist the results."""
        return self._evidence.score_evidence(case_id)

    def list_evidence_quality(self, case_id: str) -> list[EvidenceQualityAssessment]:
        return self._evidence.list_evidence_quality(case_id)

    def build_evidence_views(self, case_id: str) -> list[EvidenceView]:
        """Build reviewer-workbench views (evidence + quality + decisions)."""
        return self._evidence.build_evidence_views(case_id)

    def record_evidence_decision(
        self,
        case_id: str,
        evidence_id: str,
        reviewer: str,
        decision: EvidenceDecision | str,
        comments: str = "",
    ) -> EvidenceReviewDecision:
        """Record a reviewer APPROVE/REJECT/FLAG decision on a piece of evidence."""
        return self._evidence.record_evidence_decision(
            case_id, evidence_id, reviewer, decision, comments
        )

    def list_evidence_decisions(self, case_id: str) -> list[EvidenceReviewDecision]:
        return self._evidence.list_evidence_decisions(case_id)

    def approved_evidence(self, case_id: str) -> list[EvidenceReference]:
        """Evidence usable downstream: not rejected (approved or undecided)."""
        return self._evidence.approved_evidence(case_id)

    # ------------------------------------------------------------------ #
    # Governance + validated evidence + analytics (Milestone 11)
    # ------------------------------------------------------------------ #
    def get_governance_settings(self) -> GovernanceSettings:
        return self._governance.get_governance_settings()

    def update_governance_settings(
        self, settings: GovernanceSettings, actor: str = "admin"
    ) -> GovernanceSettings:
        """Persist governance settings and audit the change (global event)."""
        return self._governance.update_governance_settings(settings, actor)

    def build_approved_evidence_set(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> ApprovedEvidenceSet:
        """Apply governance settings to a case's evidence (audited)."""
        return self._governance.build_approved_evidence_set(case_id, settings)

    def evidence_for_consumption(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> tuple[list[EvidenceReference], ApprovedEvidenceSet]:
        """Return the evidence downstream consumers should use + the approved set."""
        return self._governance.evidence_for_consumption(case_id, settings)

    def check_compliance(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> GovernanceComplianceReport:
        """Run a governance compliance check on a case (audited)."""
        return self._governance.check_compliance(case_id, settings)

    def export_safety_gate(
        self,
        case_id: str,
        settings: GovernanceSettings | None = None,
    ) -> SafetyGateDecision:
        """Return whether a case is safe to export under active governance."""
        from app.governance.safety import SafetyGate

        settings = settings or self.get_governance_settings()
        record = self._lifecycle.require(case_id)
        compliance = self.check_compliance(case_id, settings)
        return SafetyGate(settings).export(record, compliance)

    def quality_analytics(self) -> QualityAnalytics:
        """Compute org-wide quality + workflow analytics."""
        return self._analytics.quality_analytics()

    # ------------------------------------------------------------------ #
    # Governance-enforced reviews/appeals + explainability (Milestone 13)
    # ------------------------------------------------------------------ #
    def generate_governed_review(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> GovernedReview:
        """Generate a review constrained to governance-approved evidence.

        In VALIDATED mode the review is produced from ONLY the approved evidence;
        rejected/excluded evidence cannot influence the recommendation,
        rationale, or confidence. Returns the review, its explanation, the
        approved set, and the constrained patient case.
        """
        return self._explainability.generate_review(case_id, settings)

    def generate_governed_appeal(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> GovernedAppeal:
        """Generate an appeal constrained to governance-approved evidence.

        In VALIDATED mode the appeal is drafted from ONLY the approved evidence;
        rejected/excluded evidence never appears in any appeal statement.
        """
        return self._explainability.generate_appeal(case_id, settings)

    def explain_review(
        self,
        case_id: str,
        review: ReviewResult,
        settings: GovernanceSettings | None = None,
    ) -> ReviewExplanation:
        """Build a ReviewExplanation for an existing review result."""
        self._require_explain_inputs(case_id)
        approved_set = self._governance.build_approved_evidence_set(case_id, settings)
        return self.explainability_engine.explain_review(
            case_id,
            review,
            self.evidence.for_case(case_id),
            approved_set,
            decisions_by_evidence=self._explainability._decisions_by_evidence(case_id),
            quality_by_id=self._explainability._quality_by_id(case_id),
        )

    def explain_appeal(
        self,
        case_id: str,
        appeal: AppealLetter,
        settings: GovernanceSettings | None = None,
    ) -> AppealExplanation:
        """Build an AppealExplanation for an existing appeal letter."""
        self._require_explain_inputs(case_id)
        approved_set = self._governance.build_approved_evidence_set(case_id, settings)
        return self.explainability_engine.explain_appeal(
            case_id,
            appeal,
            self.evidence.for_case(case_id),
            approved_set,
            decisions_by_evidence=self._explainability._decisions_by_evidence(case_id),
            quality_by_id=self._explainability._quality_by_id(case_id),
        )

    def traceability_chain(
        self, case_id: str, settings: GovernanceSettings | None = None
    ) -> TraceabilityChain:
        """Build the full evidence-lineage traceability chain for a case."""
        return self._explainability.traceability_chain(case_id, settings)

    # ------------------------------------------------------------------ #
    # Payer guideline packs (Final Milestone)
    # ------------------------------------------------------------------ #
    def list_payers(self) -> list[PayerProfile]:
        """Return the configured payer profiles."""
        return self._payer.list_payers()

    def get_payer(self, payer_id: str | None) -> PayerProfile:
        """Return a payer profile (or the DEFAULT profile as a fallback)."""
        return self._payer.get_payer(payer_id)

    def available_guideline_packs(self) -> list[str]:
        """Return the available guideline-pack ids (DEFAULT + payer packs)."""
        return self._payer.available_packs()

    def review_with_payer(
        self,
        case_id: str,
        payer_id: str | None = None,
        settings: GovernanceSettings | None = None,
    ) -> PayerReview:
        """Generate a governance-enforced review under a payer's guideline pack.

        The review records the payer, guideline pack, and guideline version.
        """
        return self._payer.review_with_payer(case_id, payer_id, settings)

    def appeal_with_payer(
        self,
        case_id: str,
        payer_id: str | None = None,
        settings: GovernanceSettings | None = None,
    ) -> PayerAppeal:
        """Generate a governance-enforced appeal under a payer's guideline pack.

        The appeal records the payer, guideline pack, and guideline version.
        """
        return self._payer.appeal_with_payer(case_id, payer_id, settings)

    # ------------------------------------------------------------------ #
    # Operational health (Final Milestone)
    # ------------------------------------------------------------------ #
    def operational_health(self) -> OperationalHealthReport:
        """Build a local operational-health diagnostics report."""
        return self._health.collect()

    def _require_explain_inputs(self, case_id: str) -> None:
        # Validate the case exists (raises KeyError otherwise), keeping the
        # explain_* helpers consistent with the rest of the facade.
        self.cases.get(case_id)

    # ------------------------------------------------------------------ #
    # Conflict resolution + reviewer feedback (Milestone 8)
    # ------------------------------------------------------------------ #
    def resolve_conflict(
        self,
        case_id: str,
        conflict_id: str,
        fact_type: str,
        chosen_value: str,
        rejected_values: list[str],
        reviewer_name: str,
        justification: str = "",
        source_document: str | None = None,
        source_page: int | None = None,
    ) -> tuple[ConflictResolution, AuthoritativeFact]:
        """Record a human conflict resolution and update the case."""
        return self._resolution.resolve_conflict(
            case_id,
            conflict_id,
            fact_type,
            chosen_value,
            rejected_values,
            reviewer_name,
            justification=justification,
            source_document=source_document,
            source_page=source_page,
        )

    def list_resolutions(self, case_id: str) -> list[ConflictResolution]:
        return self._resolution.list_resolutions(case_id)

    def list_authoritative_facts(self, case_id: str) -> list[AuthoritativeFact]:
        return self._resolution.list_authoritative_facts(case_id)

    def authoritative_patient_case(self, case_id: str) -> Optional[PatientCase]:
        """Return the record's patient case with authoritative facts applied."""
        return self._resolution.authoritative_patient_case(case_id)

    def record_reviewer_feedback(
        self,
        case_id: str,
        reviewer: str,
        target_type: FeedbackTarget | str,
        feedback: FeedbackVerdict | str,
        target_id: str | None = None,
        comments: str = "",
    ) -> ReviewerFeedback:
        """Record structured reviewer feedback and audit it."""
        return self._resolution.record_reviewer_feedback(
            case_id,
            reviewer,
            target_type,
            feedback,
            target_id=target_id,
            comments=comments,
        )

    def list_feedback(self, case_id: str) -> list[ReviewerFeedback]:
        return self._resolution.list_feedback(case_id)

    # ------------------------------------------------------------------ #
    # Review + appeal lifecycle
    # ------------------------------------------------------------------ #
    def attach_review(self, case_id: str, review: ReviewResult) -> CaseRecord:
        """Attach review output and move to REVIEWED."""
        return self._review.attach_review(case_id, review)

    def attach_appeal(self, case_id: str, appeal: AppealLetter) -> CaseRecord:
        """Attach appeal output, move to APPEAL_GENERATED then PENDING review."""
        evidence = self.evidence.for_case(case_id)
        if evidence:
            from app.appeals.verifier import AppealVerifier

            docs = self.documents.for_case(case_id)
            context = self.assembly.synthesize_from_evidence(case_id, evidence, docs)
            appeal = AppealVerifier().verify(appeal, context)
        return self._appeal.attach_appeal(case_id, appeal)

    def assign_reviewer(self, case_id: str, reviewer_name: str) -> CaseRecord:
        """Assign a human reviewer to a case."""
        return self._review.assign_reviewer(case_id, reviewer_name)

    def record_human_review(
        self,
        case_id: str,
        reviewer_name: str,
        decision: HumanDecision | str,
        comments: str = "",
    ) -> CaseRecord:
        """Record a human-review decision and update status accordingly."""
        return self._review.record_human_review(
            case_id, reviewer_name, decision, comments
        )

    def mark_exported(self, case_id: str) -> CaseRecord:
        """Record that a case's export package was generated."""
        settings = self.get_governance_settings()
        compliance = self.check_compliance(case_id, settings)
        return self._appeal.mark_exported(case_id, settings, compliance)

    # ------------------------------------------------------------------ #
    # Accessors
    # ------------------------------------------------------------------ #
    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        return self.cases.get(case_id)

    def list_cases(self) -> list[CaseRecord]:
        return self.cases.all()

    def history(self, case_id: str):
        return self.audit.for_case(case_id)

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()

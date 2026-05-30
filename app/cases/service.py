"""CaseService: orchestrates case lifecycle + audit logging + transitions.

This is the single entry point the UI (and tests) use to drive the workflow.
It does NOT run extraction/review/appeal agents itself - callers pass in the
already-produced artifacts. That keeps case management independent of those
engines while still recording the workflow and its audit trail.

Every significant action records an :class:`AuditEvent`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from app.audit.repository import AuditRepository
from app.cases.repository import CaseRepository, new_case_id
from app.cases.document_repository import CaseDocumentRepository
from app.cases.transitions import InvalidTransitionError, can_transition
from app.assembly.engine import CaseAssemblyEngine
from app.evidence.repository import EvidenceRepository
from app.ingestion.engine import DocumentIngestionEngine, IngestionResult
from app.ocr.repository import OCRResultRepository
from app.ocr.providers import get_ocr_provider, describe_ocr_provider
from app.vision.extractor import VisionEvidenceExtractor
from app.evidence_ai.extractor import ClaudeEvidenceExtractor
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
from app.models.appeal_letter import AppealLetter
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_document import CaseDocument, DocumentCategory, classify_document
from app.models.case_record import (
    CaseRecord,
    CaseStatus,
    HumanDecision,
    HumanReviewDecision,
)
from app.models.conflict_resolution import AuthoritativeFact, ConflictResolution
from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.evidence_reference import EvidenceReference
from app.models.evidence_review_decision import (
    EvidenceDecision,
    EvidenceReviewDecision,
)
from app.models.governance import (
    ApprovedEvidenceSet,
    GovernanceComplianceReport,
    GovernanceSettings,
)
from app.models.ocr_result import (
    DEFAULT_OCR_CONFIDENCE_THRESHOLD,
    OCRPageResult,
)
from app.models.patient_case import PatientCase
from app.models.review_result import ReviewResult
from app.models.reviewer_feedback import (
    FeedbackTarget,
    FeedbackVerdict,
    ReviewerFeedback,
)
from app.models.unified_case_context import UnifiedCaseContext
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class CaseService:
    """High-level workflow operations over cases + audit."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        # Share one connection between both repositories so a single in-memory
        # DB works for tests and a single file is used in production.
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)
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
        self.analytics = QualityAnalyticsEngine(conn=self.conn)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _set_status(
        self,
        record: CaseRecord,
        target: CaseStatus,
        actor: AuditActor = AuditActor.SYSTEM,
        log_details: Optional[str] = None,
    ) -> None:
        """Validate + apply a status transition, recording an audit event."""
        if not can_transition(record.status, target):
            raise InvalidTransitionError(
                f"Cannot move case {record.case_id} from {record.status.value} "
                f"to {target.value}."
            )
        if record.status != target:
            previous = record.status
            record.status = target
            self.audit.log(
                record.case_id,
                AuditEventType.STATUS_CHANGED,
                details=(log_details or f"Status changed: {previous.value} -> {target.value}"),
                actor=actor,
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
        record = self._require(case_id)
        record.patient_case = patient_case
        self._set_status(record, CaseStatus.EXTRACTED)
        self.audit.log(
            case_id,
            AuditEventType.EXTRACTION_COMPLETED,
            details=(
                f"Extracted case (confidence {patient_case.confidence_score:.2f})."
            ),
        )
        return self.cases.save(record)

    # ------------------------------------------------------------------ #
    # Multi-document assembly (Milestone 6/7)
    # ------------------------------------------------------------------ #
    def add_document(
        self,
        case_id: str,
        filename: str,
        raw_text: str,
        page_count: int = 1,
        document_type: DocumentCategory | str | None = None,
    ) -> CaseDocument:
        """Attach a supporting document to a case and record an audit event.

        The document type is auto-classified from filename/content when not
        explicitly provided.
        """
        self._require(case_id)
        if document_type is None:
            document_type = classify_document(filename, raw_text)
        document = CaseDocument(
            case_id=case_id,
            filename=filename,
            document_type=document_type,
            page_count=page_count,
            raw_text=raw_text,
        )
        self.documents.add(document)
        self.audit.log(
            case_id,
            AuditEventType.DOCUMENT_UPLOADED,
            details=f"Document added: {filename} ({document.document_type.value}).",
            actor=AuditActor.USER,
        )
        return document

    def list_documents(self, case_id: str) -> list[CaseDocument]:
        return self.documents.for_case(case_id)

    # ------------------------------------------------------------------ #
    # Intelligent ingestion + OCR (Milestone 9)
    # ------------------------------------------------------------------ #
    def ingest_document(
        self,
        case_id: str,
        filename: str,
        data: bytes,
        category_override: DocumentCategory | str | None = None,
        ocr_confidence_threshold: float = DEFAULT_OCR_CONFIDENCE_THRESHOLD,
    ) -> tuple[CaseDocument, IngestionResult]:
        """Ingest any supported upload (TXT/PDF/PNG/JPG/JPEG) into a case.

        Detects whether OCR is needed, runs it page-by-page when so, stores the
        document (page text joined by the page delimiter) plus the per-page OCR
        results, classifies the document, and records audit events. Low-OCR-
        confidence pages produce a warning audit entry; nothing is silently
        accepted.
        """
        self._require(case_id)
        document = CaseDocument(
            case_id=case_id,
            filename=filename,
            document_type=DocumentCategory.OTHER,  # set after ingestion
            page_count=1,
            raw_text="",
        )

        result = self.ingestion.ingest(
            filename,
            data,
            document_id=document.document_id,
            case_id=case_id,
            category_override=category_override,
        )

        # Finalize the document from the ingestion output.
        document.document_type = result.document_category
        document.page_count = max(1, result.page_count)
        document.raw_text = "\f".join(result.pages) if result.pages else ""
        self.documents.add(document)

        # Persist OCR results (if any) and stamp the case_id.
        for r in result.ocr_results:
            r.case_id = case_id
        if result.ocr_results:
            self.ocr_results.add_many(result.ocr_results)

        # Audit: document added (with ingestion kind + method).
        method = (
            result.ocr_results[0].processing_method.value
            if result.ocr_results
            else ("TEXT_LAYER" if result.kind.value != "TEXT" else "TEXT")
        )
        self.audit.log(
            case_id,
            AuditEventType.CASE_DOCUMENT_ADDED,
            details=(
                f"Ingested '{filename}' as {result.kind.value} "
                f"(type={document.document_type.value}, pages={document.page_count}, "
                f"method={method}, ocr_used={result.ocr_used})."
            ),
            actor=AuditActor.USER,
        )

        # Quality gate: warn (audit) on unavailable OCR or low-confidence pages.
        if not result.ocr_available:
            self.audit.log(
                case_id,
                AuditEventType.STATUS_CHANGED,
                details=f"OCR unavailable for '{filename}'; no text extracted.",
                actor=AuditActor.SYSTEM,
            )
        low_pages = result.low_confidence_pages(ocr_confidence_threshold)
        if low_pages:
            self.audit.log(
                case_id,
                AuditEventType.STATUS_CHANGED,
                details=(
                    f"Low-confidence OCR on '{filename}' pages {low_pages} "
                    f"(< {ocr_confidence_threshold:.0%}); flagged for reviewer "
                    "inspection."
                ),
                actor=AuditActor.SYSTEM,
            )
        for w in result.warnings:
            self.audit.log(
                case_id,
                AuditEventType.STATUS_CHANGED,
                details=f"Ingestion warning for '{filename}': {w}",
                actor=AuditActor.SYSTEM,
            )

        return document, result

    def list_ocr_results(self, case_id: str) -> list[OCRPageResult]:
        return self.ocr_results.for_case(case_id)

    def ocr_results_for_document(self, document_id: str) -> list[OCRPageResult]:
        return self.ocr_results.for_document(document_id)

    def describe_ocr(self) -> str:
        return describe_ocr_provider(self.ingestion.ocr)

    def assemble_case(self, case_id: str) -> UnifiedCaseContext:
        """Assemble all of a case's documents into a UnifiedCaseContext.

        Persists the evidence inventory, attaches the synthesized PatientCase
        (moving NEW -> EXTRACTED), and records audit events. The full context
        (conflicts, missing info) is returned for the caller/UI.
        """
        record = self._require(case_id)
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
            self._set_status(record, CaseStatus.EXTRACTED)
        self.cases.save(record)
        return context

    def list_evidence(self, case_id: str):
        return self.evidence.for_case(case_id)

    # ------------------------------------------------------------------ #
    # Evidence quality + reviewer workbench (Milestone 10)
    # ------------------------------------------------------------------ #
    def score_evidence(self, case_id: str) -> list[EvidenceQualityAssessment]:
        """Score every evidence reference for a case and persist the results."""
        self._require(case_id)
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
        self._require(case_id)
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

    # ------------------------------------------------------------------ #
    # Governance + validated evidence + analytics (Milestone 11)
    # ------------------------------------------------------------------ #
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
        self._require(case_id)
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

    def _unresolved_conflicts(self, case_id: str) -> list[str]:
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
        record = self._require(case_id)
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
            unresolved_conflict_fact_types=self._unresolved_conflicts(case_id),
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

    def quality_analytics(self) -> QualityAnalytics:
        """Compute org-wide quality + workflow analytics."""
        return self.analytics.collect()

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
        """Record a human conflict resolution and update the case.

        The reviewer's choice becomes the authoritative value; rejected values
        are preserved; the patient case on the record is updated from the
        authoritative facts; audit events are recorded by the engine.
        """
        record = self._require(case_id)
        resolution, fact = self.resolution_engine.resolve(
            case_id=case_id,
            conflict_id=conflict_id,
            fact_type=fact_type,
            chosen_value=chosen_value,
            rejected_values=rejected_values,
            reviewer_name=reviewer_name,
            justification=justification,
            source_document=source_document,
            source_page=source_page,
        )
        # Reflect authoritative facts on the stored patient case so review +
        # appeal use the human-chosen values.
        if record.patient_case is not None:
            record.patient_case = self.resolution_engine.apply_to_case(
                record.patient_case, case_id
            )
            self.cases.save(record)
        return resolution, fact

    def list_resolutions(self, case_id: str) -> list[ConflictResolution]:
        return self.resolutions.for_case(case_id)

    def list_authoritative_facts(self, case_id: str) -> list[AuthoritativeFact]:
        return self.authoritative_facts.for_case(case_id)

    def authoritative_patient_case(self, case_id: str) -> Optional[PatientCase]:
        """Return the record's patient case with authoritative facts applied."""
        record = self._require(case_id)
        if record.patient_case is None:
            return None
        return self.resolution_engine.apply_to_case(record.patient_case, case_id)

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
        self._require(case_id)
        fb = ReviewerFeedback(
            case_id=case_id,
            reviewer=reviewer,
            target_type=target_type,
            target_id=target_id,
            feedback=feedback,
            comments=comments,
        )
        self.feedback.add(fb)
        self.audit.log(
            case_id,
            AuditEventType.REVIEWER_FEEDBACK_RECORDED,
            details=(
                f"{reviewer} rated {fb.target_type.value} as {fb.feedback.value}. "
                f"{comments}"
            ).strip(),
            actor=AuditActor.USER,
        )
        return fb

    def list_feedback(self, case_id: str) -> list[ReviewerFeedback]:
        return self.feedback.for_case(case_id)

    def attach_review(self, case_id: str, review: ReviewResult) -> CaseRecord:
        """Attach review output and move to REVIEWED."""
        record = self._require(case_id)
        record.review_result = review
        self._set_status(record, CaseStatus.REVIEWED)
        self.audit.log(
            case_id,
            AuditEventType.REVIEW_COMPLETED,
            details=f"Review completed: {review.recommendation.value}.",
        )
        return self.cases.save(record)

    def attach_appeal(self, case_id: str, appeal: AppealLetter) -> CaseRecord:
        """Attach appeal output, move to APPEAL_GENERATED then PENDING review."""
        record = self._require(case_id)
        record.appeal_letter = appeal
        self._set_status(record, CaseStatus.APPEAL_GENERATED)
        self.audit.log(
            case_id,
            AuditEventType.APPEAL_GENERATED,
            details=f"Appeal generated: {appeal.appeal_id}.",
        )
        # Appeals automatically enter the human-review queue.
        self._set_status(record, CaseStatus.PENDING_HUMAN_REVIEW)
        return self.cases.save(record)

    def assign_reviewer(self, case_id: str, reviewer_name: str) -> CaseRecord:
        """Assign a human reviewer to a case."""
        record = self._require(case_id)
        record.assigned_reviewer = reviewer_name
        self.audit.log(
            case_id,
            AuditEventType.STATUS_CHANGED,
            details=f"Assigned reviewer: {reviewer_name}.",
            actor=AuditActor.USER,
        )
        return self.cases.save(record)

    def record_human_review(
        self,
        case_id: str,
        reviewer_name: str,
        decision: HumanDecision | str,
        comments: str = "",
    ) -> CaseRecord:
        """Record a human-review decision and update status accordingly."""
        record = self._require(case_id)
        review_decision = HumanReviewDecision(
            reviewer_name=reviewer_name,
            decision=decision,
            comments=comments,
        )
        record.review_decisions.append(review_decision)
        record.assigned_reviewer = reviewer_name
        if comments:
            record.review_notes = comments

        decision_enum = review_decision.decision
        if decision_enum is HumanDecision.APPROVE:
            self._set_status(
                record,
                CaseStatus.APPROVED_FOR_EXPORT,
                actor=AuditActor.USER,
                log_details=f"Approved for export by {reviewer_name}.",
            )
        elif decision_enum is HumanDecision.REJECT:
            self._set_status(
                record,
                CaseStatus.REJECTED,
                actor=AuditActor.USER,
                log_details=f"Rejected by {reviewer_name}.",
            )
        else:  # REQUEST_CHANGES
            self._set_status(
                record,
                CaseStatus.APPEAL_GENERATED,
                actor=AuditActor.USER,
                log_details=f"Changes requested by {reviewer_name}.",
            )
            # Return to the review queue after changes are requested.
            self._set_status(record, CaseStatus.PENDING_HUMAN_REVIEW)

        self.audit.log(
            case_id,
            AuditEventType.HUMAN_REVIEW_COMPLETED,
            details=f"{reviewer_name}: {decision_enum.value}. {comments}".strip(),
            actor=AuditActor.USER,
        )
        return self.cases.save(record)

    def mark_exported(self, case_id: str) -> CaseRecord:
        """Record that a case's export package was generated."""
        record = self._require(case_id)
        self.audit.log(
            case_id,
            AuditEventType.CASE_EXPORTED,
            details="Export package generated.",
            actor=AuditActor.USER,
        )
        return self.cases.save(record)

    # ------------------------------------------------------------------ #
    # Accessors
    # ------------------------------------------------------------------ #
    def _require(self, case_id: str) -> CaseRecord:
        record = self.cases.get(case_id)
        if record is None:
            raise KeyError(f"No such case: {case_id}")
        return record

    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        return self.cases.get(case_id)

    def list_cases(self) -> list[CaseRecord]:
        return self.cases.all()

    def history(self, case_id: str):
        return self.audit.for_case(case_id)

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
